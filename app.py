#@title ✨ YF TTS — Burmese AI Voice Studio ကို စတင်အသုံးပြုရန် (Play နှိပ်ပါ) { display-mode: "form" }
#@markdown ဤနေရာတွင် Code များကို ကြည့်ရန်မလိုပါ။ **ဘယ်ဘက်ရှိ Play ခလုတ်ကို နှိပ်လိုက်ရုံဖြင့်** စတင်အသုံးပြုနိုင်ပါသည်။

# ==========================================================
# 1. INSTALL PACKAGES (AUTOMATIC DEPENDENCIES)
# ==========================================================
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "voxcpm", "soundfile", "gradio", "torch", "numpy", "pydub", "pymongo", "dnspython", "cryptography"])

# ==========================================================
# 2. SECURE LIVE LICENSE VERIFICATION ENGINE
# ==========================================================
import os
import gc
import re
import time
import base64
import datetime
import hashlib
import torch
import numpy as np
import soundfile as sf
import gradio as gr
from pydub import AudioSegment
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from voxcpm import VoxCPM

# 🔒 Standard Clean Base64 Token (Database Password/URI ကို plain text မပေါ်အောင် encode ထားခြင်း)
_SEC_TOKEN = "bW9uZ29kYitzcnY6Ly9teW93aW5obGFpbmcxODRfZGJfdXNlcjp4TmtRMVJhSXYwSUZpRG1PQGNsdXN0ZXIwLnplemhrZ2IubW9uZ29kYi5uZXQvP2FwcE5hbWU9Q2x1c3RlcjA="
_DB_NAME = "vip_portal"
_COL_NAME = "vip_licenses"
DEFAULT_TOTAL_CHARACTER_QUOTA = 100_000  # old VIP records မှာ quota fields မရှိသေးရင် fallback
UNLIMITED_QUOTA_SENTINEL = -1  # UI state only; MongoDB uses unlimited_character_quota=True

def _get_secure_client():
    raw_uri = base64.b64decode(_SEC_TOKEN.encode("utf-8")).decode("utf-8")
    return MongoClient(
        raw_uri,
        serverSelectionTimeoutMS=4000,
        connectTimeoutMS=4000,
        socketTimeoutMS=5000,
        appname="VoxCPM2-User-Client"
    )


def _device_hash(device_fingerprint):
    """Hash the URL-independent browser/device fingerprint before storing it in MongoDB."""
    token = (device_fingerprint or "").strip()
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bind_or_check_device(collection, record, device_fingerprint):
    """
    First successful device binds the VIP key. Later requests must present the same
    URL-independent browser/device fingerprint. Raw fingerprint data is never stored in MongoDB; only its SHA-256 hash is stored.
    """
    token_hash = _device_hash(device_fingerprint)
    if not token_hash:
        return False, (
            "❌ Device ID မရရှိပါ။ Browser မှာ Local Storage ပိတ်ထားခြင်း/Private mode ဖြစ်နိုင်ပါသည်။ "
            "ပုံမှန် browser window ဖြင့် ပြန်ဖွင့်ပါ။"
        )

    bound_hash = str(record.get("device_id_hash") or "").strip()
    now = datetime.datetime.now(datetime.timezone.utc)

    if bound_hash:
        if bound_hash != token_hash:
            return False, (
                "🚫 ဤ VIP Key ကို အခြားဖုန်း/Browser တစ်ခုတွင် ချိတ်ထားပြီးဖြစ်ပါသည်။ "
                "ဖုန်းပြောင်းလိုပါက Admin ထံမှ Device Binding Reset လုပ်ပေးရန် လိုအပ်ပါသည်။"
            )
        collection.update_one(
            {"_id": record["_id"], "device_id_hash": token_hash},
            {"$set": {"last_device_used_at": now}},
        )
        return True, ""

    # Atomic first-device activation. This prevents two devices racing to bind one key.
    result = collection.update_one(
        {
            "_id": record["_id"],
            "$or": [
                {"device_id_hash": {"$exists": False}},
                {"device_id_hash": None},
                {"device_id_hash": ""},
            ],
        },
        {
            "$set": {
                "device_id_hash": token_hash,
                "device_bound_at": now,
                "last_device_used_at": now,
            }
        },
    )
    if result.modified_count == 1:
        return True, "📱 ဒီဖုန်း/Browser Fingerprint ကို VIP Key နဲ့ ချိတ်ပြီးပါပြီ။"

    latest = collection.find_one({"_id": record["_id"]}) or record
    if str(latest.get("device_id_hash") or "") == token_hash:
        return True, ""
    return False, (
        "🚫 ဤ VIP Key ကို အခြားဖုန်း/Browser တစ်ခုက အရင်ချိတ်သွားပါပြီ။ "
        "Admin ထံ ဆက်သွယ်ပါ။"
    )

def _normalize_quota_record(collection, record):
    """
    Normalize VIP quota state and return (total, used, remaining).

    For unlimited VIP keys:
      total = UNLIMITED_QUOTA_SENTINEL
      remaining = UNLIMITED_QUOTA_SENTINEL
    MongoDB itself stores unlimited_character_quota=True.
    """
    unlimited = bool(record.get("unlimited_character_quota", False))

    try:
        used = int(record.get("used_characters", 0) or 0)
    except (TypeError, ValueError):
        used = 0
    used = max(0, used)

    if unlimited:
        collection.update_one(
            {"_id": record["_id"]},
            {
                "$set": {
                    "unlimited_character_quota": True,
                    "used_characters": used,
                },
                "$unset": {
                    "total_character_quota": "",
                    "remaining_characters": "",
                },
            },
        )
        return UNLIMITED_QUOTA_SENTINEL, used, UNLIMITED_QUOTA_SENTINEL

    try:
        total = int(record.get("total_character_quota", DEFAULT_TOTAL_CHARACTER_QUOTA) or DEFAULT_TOTAL_CHARACTER_QUOTA)
    except (TypeError, ValueError):
        total = DEFAULT_TOTAL_CHARACTER_QUOTA
    total = max(1, total)
    used = min(used, total)

    try:
        remaining = int(record.get("remaining_characters", total - used))
    except (TypeError, ValueError):
        remaining = total - used
    remaining = max(0, min(remaining, total - used))

    collection.update_one(
        {"_id": record["_id"]},
        {"$set": {
            "unlimited_character_quota": False,
            "total_character_quota": total,
            "used_characters": used,
            "remaining_characters": remaining,
        }},
    )
    return total, used, remaining


def verify_vip_license(key_str, device_fingerprint):
    """
    Return (is_valid, message, total_quota, used, remaining).

    total_quota / remaining are -1 when the VIP key has unlimited character quota.
    VIP is still restricted by expiry date and one device fingerprint.
    """
    if not key_str or not key_str.strip():
        return False, "❌ VIP License Key ထည့်သွင်းပေးပါရန်", 0, 0, 0

    clean_key = key_str.strip()
    client = None

    try:
        client = _get_secure_client()
        collection = client[_DB_NAME][_COL_NAME]
        record = collection.find_one({"vip_key": clean_key})

        if not record:
            return False, "❌ ဤ VIP Key သည် မရှိပါ၊ ဖျက်ပြီးသားဖြစ်ပါသည် သို့မဟုတ် သက်တမ်းကုန်ပြီး Auto Delete ဖြစ်သွားပါပြီ", 0, 0, 0

        if record.get("status") != "active":
            return False, "❌ ဤ VIP Key သည် အသုံးပြုခွင့် ပိတ်ထားခံရပါသည်", 0, 0, 0

        expires_at = record.get("expires_at")
        now = datetime.datetime.now(datetime.timezone.utc)
        if not expires_at:
            return False, "❌ VIP သက်တမ်း အချက်အလက် မမှန်ကန်ပါ", 0, 0, 0
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
        if now > expires_at:
            return False, f"⌛ သင့် VIP သက်တမ်းသည် ({expires_at.strftime('%Y-%m-%d')}) တွင် ကုန်ဆုံးသွားပါပြီ", 0, 0, 0

        device_ok, device_msg = _bind_or_check_device(collection, record, device_fingerprint)
        if not device_ok:
            return False, device_msg, 0, 0, 0

        record = collection.find_one({"_id": record["_id"]}) or record
        total, used, remaining = _normalize_quota_record(collection, record)
        unlimited = total == UNLIMITED_QUOTA_SENTINEL

        days_left = max(0, (expires_at.date() - now.date()).days)
        user_name = record.get("user_name", "VIP Member")
        binding_note = f"  \n{device_msg}" if device_msg else ""

        if unlimited:
            quota_line = f"♾️ Character Quota: **Unlimited** · Used: **{used:,}**"
        else:
            quota_line = (
                f"🔤 Total Quota: **{total:,}** · Used: **{used:,}** · "
                f"Remaining: **{remaining:,}**"
            )

        msg = (
            f"👑 VIP Access အတည်ပြုပြီးပါပြီ!  \n"
            f"အသုံးပြုသူ: **{user_name}** · သက်တမ်းကျန်: **{days_left} ရက်**  \n"
            f"📱 Device: **ဒီဖုန်း/Browser တစ်ခုတည်း**  \n"
            f"{quota_line}"
            f"{binding_note}"
        )
        return True, msg, total, used, remaining

    except PyMongoError:
        return False, "❌ Database ချိတ်ဆက်၍ မရပါ။ အင်တာနက်လိုင်းကို စစ်ဆေးပေးပါ", 0, 0, 0
    except Exception as e:
        return False, f"❌ စစ်ဆေးမှု မအောင်မြင်ပါ: {str(e)}", 0, 0, 0
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def quota_counter_html(text, total_quota, remaining_quota):
    text = text or ""
    request_chars = len(text.strip())

    try:
        total = int(total_quota)
    except (TypeError, ValueError):
        total = 0
    try:
        remaining = int(remaining_quota)
    except (TypeError, ValueError):
        remaining = 0

    if total == UNLIMITED_QUOTA_SENTINEL:
        return (
            '<div class="char-counter ok">'
            f'♾️ Character Quota <b>Unlimited</b> · ယခု Generate: <b>{request_chars:,}</b> characters'
            '</div>'
        )

    if total <= 0:
        return (
            '<div class="char-counter neutral">'
            f'📝 ယခုစာသား <b>{request_chars:,}</b> characters · VIP Key ကို Verify လုပ်ပြီး Quota ကို ရယူပါ'
            '</div>'
        )

    used = max(0, total - remaining)
    if request_chars > remaining:
        return (
            '<div class="char-counter over">'
            f'🚫 ယခုစာသား <b>{request_chars:,}</b> · Remaining <b>{remaining:,}</b> · '
            f'Quota မလုံလောက်ပါ (<b>{request_chars - remaining:,}</b> လုံးလိုအပ်နေသေးသည်)'
            '</div>'
        )

    return (
        '<div class="char-counter ok">'
        f'🔤 Total <b>{total:,}</b> · Used <b>{used:,}</b> · Remaining <b>{remaining:,}</b> · '
        f'ယခု Generate: <b>{request_chars:,}</b>'
        '</div>'
    )


def verify_vip_for_ui(vip_key, device_fingerprint, text):
    is_valid, msg, total, used, remaining = verify_vip_license(vip_key, device_fingerprint)
    if not is_valid:
        return msg, 0, 0, quota_counter_html(text, 0, 0)
    return msg, total, remaining, quota_counter_html(text, total, remaining)


def update_quota_counter(text, total_quota, remaining_quota):
    return quota_counter_html(text, total_quota, remaining_quota)


def reserve_vip_quota(vip_key, device_fingerprint, request_chars):
    """
    Atomically reserve character usage for the bound device.

    Finite VIP: checks and deducts remaining_characters.
    Unlimited VIP: never blocks by character count; only increments used_characters
    for usage reporting.
    """
    client = None
    try:
        client = _get_secure_client()
        collection = client[_DB_NAME][_COL_NAME]
        record = collection.find_one({"vip_key": vip_key})
        if not record:
            return False, "❌ VIP Key မတွေ့ပါ။ သက်တမ်းကုန်ပြီး Auto Delete ဖြစ်ထားနိုင်ပါသည်။", 0, 0, 0

        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = record.get("expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
        if not expires_at or now > expires_at:
            return False, "⌛ VIP Key သက်တမ်းကုန်ဆုံးသွားပါပြီ။", 0, 0, 0

        device_ok, device_msg = _bind_or_check_device(collection, record, device_fingerprint)
        if not device_ok:
            return False, device_msg, 0, 0, 0

        token_hash = _device_hash(device_fingerprint)
        record = collection.find_one({"_id": record["_id"]}) or record
        total, used, remaining = _normalize_quota_record(collection, record)

        if request_chars <= 0:
            return False, "❌ အသုံးပြုမည့်စာသား မရှိပါ။", total, used, remaining

        unlimited = total == UNLIMITED_QUOTA_SENTINEL

        if unlimited:
            updated = collection.find_one_and_update(
                {
                    "vip_key": vip_key,
                    "status": "active",
                    "device_id_hash": token_hash,
                    "expires_at": {"$gte": now},
                    "unlimited_character_quota": True,
                },
                {
                    "$inc": {"used_characters": int(request_chars)},
                    "$set": {
                        "last_used_at": now,
                        "last_device_used_at": now,
                    },
                },
                return_document=True,
            )
            if not updated:
                return False, "🚫 Unlimited VIP Device/Expiry စစ်ဆေးမှု မအောင်မြင်ပါ။", total, used, remaining

            total, used, remaining = _normalize_quota_record(collection, updated)
            return True, "", total, used, remaining

        updated = collection.find_one_and_update(
            {
                "vip_key": vip_key,
                "status": "active",
                "device_id_hash": token_hash,
                "expires_at": {"$gte": now},
                "unlimited_character_quota": {"$ne": True},
                "remaining_characters": {"$gte": int(request_chars)},
            },
            {
                "$inc": {
                    "used_characters": int(request_chars),
                    "remaining_characters": -int(request_chars),
                },
                "$set": {
                    "last_used_at": now,
                    "last_device_used_at": now,
                },
            },
            return_document=True,
        )
        if not updated:
            latest = collection.find_one({"vip_key": vip_key}) or record
            total, used, remaining = _normalize_quota_record(collection, latest)
            return False, (
                f"🚫 Total Character Quota မလုံလောက်ပါ သို့မဟုတ် Device/Expiry စစ်ဆေးမှု မအောင်မြင်ပါ။  \n"
                f"လိုအပ်: **{request_chars:,}** · Remaining: **{remaining:,}**"
            ), total, used, remaining

        total, used, remaining = _normalize_quota_record(collection, updated)
        return True, "", total, used, remaining

    except Exception as e:
        return False, f"❌ Quota/Device စစ်ဆေးမှု မအောင်မြင်ပါ: {e}", 0, 0, 0
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def release_vip_quota(vip_key, request_chars):
    """Refund reserved usage when no audio was produced."""
    client = None
    try:
        client = _get_secure_client()
        collection = client[_DB_NAME][_COL_NAME]
        record = collection.find_one({"vip_key": vip_key})
        if not record:
            return

        total, used, remaining = _normalize_quota_record(collection, record)
        refund = min(int(request_chars), used)
        if refund <= 0:
            return

        if total == UNLIMITED_QUOTA_SENTINEL:
            update = {
                "$inc": {"used_characters": -refund},
                "$set": {"last_refund_at": datetime.datetime.now(datetime.timezone.utc)},
            }
        else:
            update = {
                "$inc": {
                    "used_characters": -refund,
                    "remaining_characters": refund,
                },
                "$set": {"last_refund_at": datetime.datetime.now(datetime.timezone.utc)},
            }

        collection.update_one({"vip_key": vip_key}, update)

    except Exception as e:
        print(f"[QUOTA REFUND ERROR] {type(e).__name__}: {e}")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


# ==========================================================
# 3. LOAD VOXCPM2 MODEL ON GPU
# ==========================================================
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"🚀 Running on Device: {device.upper()}")
print("⏳ VoxCPM2 Model ကို GPU ပေါ်သို့ စတင်ဆွဲတင်နေပါသည်...")
model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
print("✅ VoxCPM2 Model Loaded Successfully (Ready for 30+ Mins Audio)!")

def split_burmese_text_long(text, max_chars=90):
    raw_sentences = re.split(r'([။၊\n?!])', text)
    chunks, curr = [], ""
    for item in raw_sentences:
        curr += item
        if item in ['။', '၊', '\n', '?', '!']:
            if curr.strip():
                if len(curr) > max_chars:
                    words = curr.split(' ')
                    sub = ""
                    for w in words:
                        if len(sub) + len(w) <= max_chars:
                            sub += (" " if sub else "") + w
                        else:
                            if sub.strip():
                                chunks.append(sub.strip())
                            sub = w
                    if sub.strip():
                        chunks.append(sub.strip())
                else:
                    chunks.append(curr.strip())
                curr = ""
    if curr.strip():
        chunks.append(curr.strip())
    return [c for c in chunks if c.strip()]

# ==========================================================
# 4. ULTRA LONG-TEXT GENERATION PIPELINE
# ==========================================================
def generate_vip_long(vip_key, device_fingerprint, text, control_instruction, reference_audio, use_reference_transcript, reference_text, clone_strength, progress=gr.Progress()):
    is_valid, auth_msg, total_quota, used_before, remaining_before = verify_vip_license(vip_key, device_fingerprint)
    if not is_valid:
        return None, "", auth_msg, 0, 0, quota_counter_html(text, 0, 0)

    if not text or not text.strip() or not reference_audio:
        return None, "", "❌ စာသားနှင့် နမူနာအသံဖိုင် ထည့်သွင်းပေးပါ", total_quota, remaining_before, quota_counter_html(text, total_quota, remaining_before)

    clean_text = text.strip()
    request_chars = len(clean_text)

    # No fixed per-generation limit. Only the VIP key's remaining TOTAL quota matters.
    reserved, reserve_msg, total_quota, used_after_reserve, remaining_after_reserve = reserve_vip_quota(vip_key.strip(), device_fingerprint, request_chars)
    if not reserved:
        return None, "", reserve_msg, total_quota, remaining_after_reserve, quota_counter_html(text, total_quota, remaining_after_reserve)

    chunks = split_burmese_text_long(clean_text, max_chars=90) or [clean_text]
    prompt_text = reference_text if (use_reference_transcript and reference_text) else None

    audio_segments = []
    silence_gap = 0.15

    total = len(chunks)
    start_all = time.time()

    for idx, chunk in enumerate(chunks):
        pct = (idx + 1) / total
        elapsed = time.time() - start_all
        est_total = (elapsed / (idx + 1)) * total
        rem_sec = max(0, int(est_total - elapsed))
        rem_min = rem_sec // 60

        progress(pct, desc=f"🎙️ စာကြောင်း ({idx+1}/{total}) ထုတ်လုပ်နေပါသည်... (ခန့်မှန်းကျန်: {rem_min} မိနစ် {rem_sec%60} စက္ကန့်)")
        full_chunk_text = f"({control_instruction}){chunk}" if control_instruction else chunk

        try:
            if prompt_text and idx == 0:
                wav = model.generate(
                    text=full_chunk_text,
                    prompt_wav_path=reference_audio,
                    prompt_text=prompt_text,
                    reference_wav_path=reference_audio,
                    cfg_value=float(clone_strength)
                )
            else:
                wav = model.generate(
                    text=full_chunk_text,
                    reference_wav_path=reference_audio,
                    cfg_value=float(clone_strength)
                )

            audio_segments.append(wav)
            silence_samples = int(model.tts_model.sample_rate * silence_gap)
            audio_segments.append(np.zeros(silence_samples, dtype=np.float32))

            if idx % 10 == 0:
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

        except Exception as e:
            print(f"Chunk #{idx+1} Error: {e}")
            continue

    if not audio_segments:
        release_vip_quota(vip_key.strip(), request_chars)
        _, _, total2, used2, remaining2 = verify_vip_license(vip_key, device_fingerprint)
        return None, "", "❌ အသံထုတ်လုပ်ခြင်း မအောင်မြင်ပါ။ အသုံးပြုစာလုံး quota ကို ပြန်ဖြည့်ပေးထားပါသည်။", total2, remaining2, quota_counter_html(text, total2, remaining2)

    final_wav = np.concatenate(audio_segments)
    ts = int(time.time() * 1000)

    # Export MP3
    temp_wav_path = f"temp_{ts}.wav"
    sf.write(temp_wav_path, final_wav, model.tts_model.sample_rate)
    output_mp3_path = f"cloned_voice_{ts}.mp3"
    audio_segment = AudioSegment.from_wav(temp_wav_path)
    audio_segment.export(output_mp3_path, format="mp3", bitrate="192k")
    if os.path.exists(temp_wav_path):
        os.remove(temp_wav_path)

    # MP3 direct download
    with open(output_mp3_path, "rb") as f:
        mp3_b64 = base64.b64encode(f.read()).decode()

    download_buttons_html = f"""
    <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:15px;">
        <a href="data:audio/mp3;base64,{mp3_b64}" download="Long_Voice_{ts}.mp3" style="background:#8B5CF6; color:white; padding:12px 20px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:15px; display:inline-flex; align-items:center; gap:6px;">
            📥 MP3 အသံဖိုင် တိုက်ရိုက်ဒေါင်းလုဒ် (.mp3)
        </a>
    </div>
    """

    duration_sec = len(final_wav) / model.tts_model.sample_rate
    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)

    _, _, total_final, used_final, remaining_final = verify_vip_license(vip_key, device_fingerprint)
    usage_line = (
        f"📊 **VIP Usage:** **Unlimited ♾️** · Used **{used_final:,}**"
        if total_final == UNLIMITED_QUOTA_SENTINEL
        else f"📊 **VIP Usage:** **{used_final:,} / {total_final:,}** · Remaining **{remaining_final:,}**"
    )
    status_text = (
        f"🎉 {auth_msg}\n\n"
        f"✅ **စာကြောင်းပေါင်း ({total}) ကြောင်း အပြည့်အစုံ အောင်မြင်စွာ ထုတ်လုပ်ပြီးပါပြီ!**\n"
        f"🔤 **ဒီတစ်ကြိမ် အသုံးပြုစာလုံး:** **{request_chars:,}**\n"
        f"{usage_line}\n"
        f"⏱️ **စုစုပေါင်း အသံကြာချိန်:** **{mins} မိနစ် {secs} စက္ကန့်**"
    )

    return output_mp3_path, download_buttons_html, status_text, total_final, remaining_final, quota_counter_html("", total_final, remaining_final)

# ==========================================================
# 5. YF TTS · LUXURY BLACK & GOLD GRADIO UI
# ==========================================================
APP_CSS = """
body {
    background:
        radial-gradient(circle at 8% 3%, rgba(255, 198, 76, .15), transparent 31%),
        radial-gradient(circle at 92% 10%, rgba(174, 125, 28, .14), transparent 28%),
        linear-gradient(150deg, #070707, #11100c 55%, #090909);
    color: #f8f3e7;
}
.gradio-container {
    max-width: 1180px !important;
    margin: 0 auto !important;
    padding: 28px 18px 44px !important;
}
.hero-card {
    position: relative;
    overflow: hidden;
    padding: 34px 34px 30px;
    border: 1px solid rgba(235, 190, 82, .37);
    border-radius: 24px;
    background: linear-gradient(118deg, rgba(36, 29, 12, .96), rgba(17, 16, 13, .96) 58%, rgba(57, 40, 11, .78));
    box-shadow: 0 22px 60px rgba(0, 0, 0, .42), inset 0 1px 0 rgba(255, 230, 169, .10);
    margin-bottom: 20px;
}
.hero-card::after {
    content: "YF";
    position: absolute;
    right: 30px;
    top: -44px;
    color: rgba(255, 212, 118, .07);
    font-size: 168px;
    font-weight: 800;
    letter-spacing: -16px;
    pointer-events: none;
}
.brand-kicker { color: #e4b650; font-size: 12px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase; }
.hero-card h1 { position: relative; margin: 7px 0 9px; font-size: clamp(28px, 4vw, 43px); color: #fff9e9 !important; }
.hero-card p { position: relative; max-width: 690px; margin: 0; color: #d5cdbc; line-height: 1.75; }
.feature-row { position: relative; display: flex; flex-wrap: wrap; gap: 9px; margin-top: 19px; }
.feature-pill {
    padding: 7px 12px;
    border: 1px solid rgba(238, 195, 90, .27);
    border-radius: 999px;
    background: rgba(242, 190, 66, .08);
    color: #f4d890;
    font-size: 13px;
}
.panel {
    border: 1px solid rgba(231, 191, 97, .17) !important;
    border-radius: 20px !important;
    padding: 20px !important;
    background: linear-gradient(145deg, rgba(30, 29, 25, .92), rgba(16, 16, 15, .94)) !important;
    box-shadow: 0 16px 42px rgba(0, 0, 0, .27);
}
.section-number { color: #e5b44c; font-size: 12px; font-weight: 800; letter-spacing: 1.4px; }
.section-title h3 { margin: 3px 0 2px; color: #fff7e2 !important; font-size: 20px; font-weight: 700; }
.section-title p { margin: 0 0 15px; color: #aaa18f; font-size: 14px; }
#generate-btn, #generate-btn button {
    min-height: 54px;
    border: 1px solid rgba(255, 235, 182, .42) !important;
    border-radius: 13px !important;
    background: linear-gradient(105deg, #bb7b13, #f0c55d 52%, #bb7b13) !important;
    color: #1b1203 !important;
    box-shadow: 0 12px 28px rgba(193, 130, 16, .26);
    font-size: 16px !important;
    font-weight: 800 !important;
}
#generate-btn {
    width: 100%;
}
#generate-btn:hover, #generate-btn button:hover { transform: translateY(-1px); filter: brightness(1.08); }
.char-counter {
    margin: 8px 0 4px;
    padding: 10px 13px;
    border-radius: 10px;
    font-size: 13px;
    line-height: 1.5;
    border: 1px solid rgba(231, 191, 97, .18);
    background: rgba(255,255,255,.035);
}
.char-counter.ok { color: #9ce8b6; border-color: rgba(16,185,129,.32); background: rgba(16,185,129,.08); }
.char-counter.over { color: #ffaaa5; border-color: rgba(239,68,68,.38); background: rgba(239,68,68,.09); }
.char-counter.neutral { color: #d7cba9; }
.footer-note { text-align: center; color: #8f856f; font-size: 12px; margin-top: 18px; }
@media (max-width: 700px) {
    .gradio-container { padding: 12px 10px 28px !important; }
    .hero-card { padding: 24px 19px; border-radius: 17px; }
    .panel { padding: 13px !important; border-radius: 15px !important; }
    .hero-card::after { right: 15px; font-size: 114px; }
}
"""

APP_THEME = gr.themes.Soft(
    primary_hue="amber",
    secondary_hue="yellow",
    neutral_hue="stone",
)

GET_DEVICE_FINGERPRINT_JS = r"""
(vip, device, ...rest) => {
    // URL-independent browser/device signature for Colab/Gradio share links.
    // This is NOT the phone's biometric fingerprint and does not read IMEI/serial numbers.
    function safe(fn, fallback = "") {
        try {
            const v = fn();
            return (v === undefined || v === null) ? fallback : String(v);
        } catch (e) {
            return fallback;
        }
    }

    function browserFamily() {
        const ua = navigator.userAgent || "";
        if (/SamsungBrowser/i.test(ua)) return "SamsungInternet";
        if (/EdgA|EdgiOS|Edg\//i.test(ua)) return "Edge";
        if (/OPR\//i.test(ua)) return "Opera";
        if (/CriOS|Chrome\//i.test(ua)) return "Chrome";
        if (/FxiOS|Firefox\//i.test(ua)) return "Firefox";
        if (/Safari\//i.test(ua) && !/Chrome|CriOS|Chromium/i.test(ua)) return "Safari";
        return "Other";
    }

    function tinyHash(str) {
        // Compact synchronous non-secret hash. Python applies SHA-256 before DB storage.
        let h1 = 0x811c9dc5;
        let h2 = 0x9e3779b9;
        for (let i = 0; i < str.length; i++) {
            const c = str.charCodeAt(i);
            h1 ^= c;
            h1 = Math.imul(h1, 0x01000193) >>> 0;
            h2 ^= (c + i) >>> 0;
            h2 = Math.imul(h2, 0x85ebca6b) >>> 0;
        }
        return h1.toString(16).padStart(8, "0") + h2.toString(16).padStart(8, "0");
    }

    function canvasSignature() {
        try {
            const c = document.createElement("canvas");
            c.width = 280;
            c.height = 60;
            const ctx = c.getContext("2d");
            if (!ctx) return "no-canvas";
            ctx.textBaseline = "top";
            ctx.font = "16px Arial";
            ctx.fillStyle = "#f60";
            ctx.fillRect(4, 4, 120, 24);
            ctx.fillStyle = "#069";
            ctx.fillText("YF-TTS-Device-2026 မြန်မာ", 7, 8);
            ctx.globalCompositeOperation = "multiply";
            ctx.fillStyle = "rgba(120,80,220,.65)";
            ctx.beginPath();
            ctx.arc(180, 27, 18, 0, Math.PI * 2);
            ctx.fill();
            return tinyHash(c.toDataURL());
        } catch (e) {
            return "canvas-blocked";
        }
    }

    function webglSignature() {
        try {
            const c = document.createElement("canvas");
            const gl = c.getContext("webgl") || c.getContext("experimental-webgl");
            if (!gl) return "no-webgl";
            const ext = gl.getExtension("WEBGL_debug_renderer_info");
            const vendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
            const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
            return tinyHash(String(vendor) + "|" + String(renderer));
        } catch (e) {
            return "webgl-blocked";
        }
    }

    const sw = Number(screen.width || 0);
    const sh = Number(screen.height || 0);
    // Normalize orientation so rotating the phone does not change the signature.
    const screenShort = Math.min(sw, sh);
    const screenLong = Math.max(sw, sh);

    const uaDataPlatform = safe(() => navigator.userAgentData && navigator.userAgentData.platform, "");
    const uaDataMobile = safe(() => navigator.userAgentData && navigator.userAgentData.mobile, "");

    const parts = [
        "yf-device-fingerprint-v2",
        uaDataPlatform || safe(() => navigator.platform, "unknown"),
        uaDataMobile || (/Mobi|Android|iPhone|iPad/i.test(navigator.userAgent || "") ? "mobile" : "desktop"),
        browserFamily(),
        `${screenShort}x${screenLong}`,
        safe(() => screen.colorDepth, "0"),
        safe(() => navigator.hardwareConcurrency, "0"),
        safe(() => navigator.deviceMemory, "0"),
        safe(() => navigator.maxTouchPoints, "0"),
        canvasSignature(),
        webglSignature(),
    ];

    const fingerprint = parts.join("|");
    return [vip, fingerprint, ...rest];
}
"""

with gr.Blocks(title="YF TTS · Burmese AI Voice Studio", theme=APP_THEME, css=APP_CSS) as demo:
    vip_total_quota = gr.State(0)
    vip_remaining_quota = gr.State(0)
    device_fingerprint = gr.Textbox(value="", visible=False, label="Device Fingerprint")
    gr.HTML("""
    <section class="hero-card">
        <div class="brand-kicker">YF TTS · Burmese AI Voice Studio</div>
        <h1>🎙️ သင့်အသံဖြင့် စာသားတိုင်းကို အသက်သွင်းပါ</h1>
        <p>စာမျက်နှာရှည် ဇာတ်ညွှန်းများနှင့် စာအုပ်များကို သင့်နမူနာအသံဖြင့်
        MP3 အသံဖိုင်အဖြစ် ထုတ်လုပ်ပါ။</p>
        <div class="feature-row">
            <span class="feature-pill">⚡ GPU Powered</span>
            <span class="feature-pill">🎧 Voice Cloning</span>
            <span class="feature-pill">🎵 MP3 Output</span>
            <span class="feature-pill">⏱️ Long-form Ready</span>
            <span class="feature-pill">👑 VIP Access</span>
        </div>
    </section>
    """)

    with gr.Row(equal_height=False):
        with gr.Column(scale=6, elem_classes="panel"):
            gr.HTML("""
            <div class="section-title">
                <div class="section-number">STEP 01</div>
                <h3>အသံထုတ်လုပ်ရန်</h3>
                <p>VIP Key၊ ဖတ်စေလိုသောစာနှင့် နမူနာအသံကို ထည့်ပါ။</p>
            </div>
            """)
            vip_key = gr.Textbox(
                label="🔑 VIP License Key",
                placeholder="VIP-USER01-20260430-XXXXXXXX",
                type="password",
            )
            verify_vip_btn = gr.Button("👑 VIP Key Verify + Quota ရယူမည်", variant="secondary")
            vip_verify_status = gr.Markdown("VIP Key ကို Verify လုပ်ပြီး သင့် Total Character Quota ကို ရယူပါ။")

            text_in = gr.Textbox(
                label="📝 ဖတ်စေလိုသော စာသား",
                lines=13,
                placeholder="ဇာတ်ညွှန်း သို့မဟုတ် စာပိုဒ်အရှည်ကို ဤနေရာတွင် ကူးထည့်ပါ...",
            )
            char_counter = gr.HTML(quota_counter_html("", 0, 0))
            audio_in = gr.Audio(
                type="filepath",
                label="🎤 နမူနာအသံ (5–15 seconds)",
            )

            with gr.Accordion("⚙️ Advanced Voice Settings", open=False):
                control_in = gr.Textbox(
                    label="အသံပုံစံညွှန်ကြားချက် (Optional)",
                    placeholder="ဥပမာ — cheerful, calm, whisper, fast",
                )
                clone_str = gr.Slider(
                    minimum=1.0,
                    maximum=3.0,
                    value=2.8,
                    step=0.1,
                    label="Clone Strength",
                )
                use_transcript = gr.Checkbox(
                    label="နမူနာအသံ၏ မူရင်းစာသားကို အသုံးပြုမည်",
                    value=False,
                )
                ref_text_in = gr.Textbox(
                    label="နမူနာအသံ၏ မူရင်းစာသား (Optional)",
                    lines=2,
                )

            gen_btn = gr.Button(
                "✨ အသံ စတင်ထုတ်လုပ်မည်",
                variant="primary",
                elem_id="generate-btn",
            )

        with gr.Column(scale=5, elem_classes="panel"):
            gr.HTML("""
            <div class="section-title">
                <div class="section-number">STEP 02</div>
                <h3>ရလဒ်</h3>
                <p>လုပ်ဆောင်မှုအခြေအနေနှင့် ထွက်ရှိလာသောအသံကို ဒီမှာကြည့်ပါ။</p>
            </div>
            """)
            status_markdown = gr.Markdown("အသံထုတ်လုပ်ရန် အဆင်သင့်ဖြစ်ပါပြီ။")
            audio_preview = gr.Audio(
                label="🎧 အသံရလဒ်ကို နားဆင်ရန်",
                type="filepath",
            )
            direct_download_html = gr.HTML()

    gr.HTML('<div class="footer-note">YF TTS · Burmese AI Voice Studio · VIP Access</div>')

    verify_vip_btn.click(
        fn=verify_vip_for_ui,
        inputs=[vip_key, device_fingerprint, text_in],
        outputs=[vip_verify_status, vip_total_quota, vip_remaining_quota, char_counter],
        js=GET_DEVICE_FINGERPRINT_JS,
    )

    vip_key.submit(
        fn=verify_vip_for_ui,
        inputs=[vip_key, device_fingerprint, text_in],
        outputs=[vip_verify_status, vip_total_quota, vip_remaining_quota, char_counter],
        js=GET_DEVICE_FINGERPRINT_JS,
    )

    text_in.input(
        fn=update_quota_counter,
        inputs=[text_in, vip_total_quota, vip_remaining_quota],
        outputs=[char_counter],
    )

    gen_btn.click(
        fn=generate_vip_long,
        inputs=[vip_key, device_fingerprint, text_in, control_in, audio_in, use_transcript, ref_text_in, clone_str],
        outputs=[audio_preview, direct_download_html, status_markdown, vip_total_quota, vip_remaining_quota, char_counter],
        js=GET_DEVICE_FINGERPRINT_JS,
    )

demo.queue().launch(
    share=True,
    debug=True,
)
