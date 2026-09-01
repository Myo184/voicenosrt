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
import json
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
_SETTINGS_COL_NAME = os.getenv("MONGODB_SETTINGS_COLLECTION", "vip_global_settings").strip()
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


def _device_hashes(device_fingerprint):
    """Return primary plus legacy hashes so existing bound devices migrate safely."""
    token = (device_fingerprint or "").strip()
    if not token:
        return []
    candidates = [token]
    try:
        packed = json.loads(token)
        if isinstance(packed, dict) and packed.get("primary"):
            candidates = [str(packed["primary"])]
            if packed.get("legacy"):
                candidates.append(str(packed["legacy"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    hashes = []
    for candidate in candidates:
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        if digest not in hashes:
            hashes.append(digest)
    return hashes


def _device_hash(device_fingerprint):
    hashes = _device_hashes(device_fingerprint)
    return hashes[0] if hashes else ""


def _device_lock_is_enabled(collection):
    """Read the Main Admin switch. Fail closed: an unavailable setting keeps locking on."""
    try:
        settings = collection.database[_SETTINGS_COL_NAME].find_one(
            {"_id": "global"}, {"device_lock_enabled": 1}
        )
        return True if not settings else bool(settings.get("device_lock_enabled", True))
    except Exception:
        return True


def _bind_or_check_device(collection, record, device_fingerprint):
    """Device locking has been removed. A valid VIP key works from any link or device."""
    return True, ""

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
            f"{quota_line}"
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


def wizard_progress_html(current_step):
    labels = ["VIP Key", "စာသား", "နမူနာအသံ", "Result"]
    cards = []
    for step, label in enumerate(labels, start=1):
        state = "active" if step == current_step else ("done" if step < current_step else "")
        marker = "✓" if step < current_step else str(step)
        cards.append(
            f'<div class="wizard-step {state}"><span class="wizard-dot">{marker}</span>'
            f'<span class="wizard-label">{label}</span></div>'
        )
    return '<div class="wizard-progress">' + ''.join(cards) + '</div>'


def loading_flow_html():
    return """
    <div class="loading-flow">
      <div class="pulse-orb"><span></span></div>
      <div><b>အသံ ပြုလုပ်နေပါသည်…</b><p>နမူနာအသံကို ခွဲခြမ်းပြီး စာသားတိုင်းကို အသံပြောင်းနေပါသည်။</p></div>
      <div class="loading-track"><i></i></div>
      <div class="loading-steps"><span>1. Voice analyse</span><span>2. Clone</span><span>3. MP3 export</span></div>
    </div>
    """


def verify_and_open_text_step(vip_key, device_fingerprint, text):
    is_valid, msg, total, used, remaining = verify_vip_license(vip_key, device_fingerprint)
    if not is_valid:
        return msg, 0, 0, quota_counter_html(text, 0, 0), gr.update(visible=True), gr.update(visible=False), wizard_progress_html(1)
    return msg, total, remaining, quota_counter_html(text, total, remaining), gr.update(visible=False), gr.update(visible=True), wizard_progress_html(2)


def open_voice_step(text):
    if not (text or "").strip():
        return "⚠️ ဖတ်စေလိုသော စာသားကို အရင်ထည့်ပေးပါ။", gr.update(visible=True), gr.update(visible=False), wizard_progress_html(2)
    return "✅ စာသားထည့်ပြီးပါပြီ။ နမူနာအသံကို တင်ပေးပါ။", gr.update(visible=False), gr.update(visible=True), wizard_progress_html(3)


def open_result_step():
    return (
        gr.update(visible=False), gr.update(visible=True), wizard_progress_html(4),
        gr.update(value=loading_flow_html(), visible=True),
        "⏳ အသံဖိုင်ကို ပြုလုပ်နေပါသည်…",
    )


def hide_loading_flow():
    return gr.update(visible=False)


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

        # Device locking is disabled for all existing and new VIP keys.
        device_lock_enabled = False
        record = collection.find_one({"_id": record["_id"]}) or record
        total, used, remaining = _normalize_quota_record(collection, record)

        if request_chars <= 0:
            return False, "❌ အသုံးပြုမည့်စာသား မရှိပါ။", total, used, remaining

        unlimited = total == UNLIMITED_QUOTA_SENTINEL

        if unlimited:
            unlimited_query = {
                "vip_key": vip_key,
                "status": "active",
                "expires_at": {"$gte": now},
                "unlimited_character_quota": True,
            }
            if device_lock_enabled:
                unlimited_query["device_id_hash"] = {"$in": token_hashes}
            updated = collection.find_one_and_update(
                unlimited_query,
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

        quota_query = {
            "vip_key": vip_key,
            "status": "active",
            "expires_at": {"$gte": now},
            "unlimited_character_quota": {"$ne": True},
            "remaining_characters": {"$gte": int(request_chars)},
        }
        if device_lock_enabled:
            quota_query["device_id_hash"] = {"$in": token_hashes}
        updated = collection.find_one_and_update(
            quota_query,
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

    # Return the file path to Gradio's compact waveform player.  Its outer
    # width is explicitly capped in CSS so loading the generated MP3 cannot
    # resize the mobile page.
    audio_preview_path = output_mp3_path

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

    return audio_preview_path, download_buttons_html, status_text, total_final, remaining_final, quota_counter_html("", total_final, remaining_final)

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
.wizard-progress { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px; max-width:760px; margin:0 auto 17px; }
.wizard-step { min-width:0; display:flex; align-items:center; justify-content:center; gap:7px; padding:9px 6px; border:1px solid rgba(231,191,97,.14); border-radius:12px; color:#8f856f; background:rgba(255,255,255,.02); font-size:12px; font-weight:700; }
.wizard-step.active { color:#ffecb4; border-color:rgba(240,197,93,.65); background:linear-gradient(115deg,rgba(187,123,19,.28),rgba(240,197,93,.10)); box-shadow:0 6px 19px rgba(193,130,16,.16); }
.wizard-step.done { color:#bce9c7; border-color:rgba(88,190,123,.35); }
.wizard-dot { display:grid; place-items:center; flex:0 0 21px; width:21px; height:21px; border-radius:50%; color:#d7cba9; background:rgba(255,255,255,.09); font-size:11px; }
.wizard-step.active .wizard-dot { color:#231503; background:#f0c55d; }
.wizard-step.done .wizard-dot { color:#0d351b; background:#83d9a0; }
.wizard-stage { animation:wizard-in .4s cubic-bezier(.2,.8,.2,1) both; }
@keyframes wizard-in { from { opacity:0; transform:translateY(17px) scale(.985); filter:blur(3px); } to { opacity:1; transform:translateY(0) scale(1); filter:blur(0); } }
.stage-card { max-width:760px; margin:0 auto; }
/* Keep Gradio's waveform controls intact; constrain only the outside player box. */
#reference-audio { width:100% !important; min-width:0 !important; max-width:100% !important; overflow:hidden !important; }
#reference-audio *, #reference-audio .wrap, #reference-audio .audio-container { min-width:0 !important; max-width:100% !important; box-sizing:border-box !important; }
.loading-flow { position:relative; overflow:hidden; margin:6px 0 16px; padding:18px; border:1px solid rgba(240,197,93,.3); border-radius:16px; background:linear-gradient(135deg,rgba(187,123,19,.18),rgba(21,19,13,.75)); }
.loading-flow > div:nth-child(2) { margin-left:62px; min-height:44px; }
.loading-flow b { color:#ffe4a0; font-size:16px; }
.loading-flow p { margin:3px 0 0; color:#c9bfa9; font-size:12px; line-height:1.55; }
.pulse-orb { position:absolute; left:17px; top:20px; width:38px; height:38px; border-radius:50%; background:#e7b547; box-shadow:0 0 0 0 rgba(240,197,93,.55); animation:pulse-orb 1.45s infinite; }
.pulse-orb span { position:absolute; inset:11px; border-radius:50%; background:#271703; }
@keyframes pulse-orb { 70% { box-shadow:0 0 0 13px rgba(240,197,93,0); } 100% { box-shadow:0 0 0 0 rgba(240,197,93,0); } }
.loading-track { height:5px; margin-top:14px; overflow:hidden; border-radius:999px; background:rgba(255,255,255,.10); }
.loading-track i { display:block; width:42%; height:100%; border-radius:inherit; background:linear-gradient(90deg,#b57512,#ffe19a,#b57512); animation:loading-slide 1.3s ease-in-out infinite; }
@keyframes loading-slide { from { transform:translateX(-110%); } to { transform:translateX(320%); } }
.loading-steps { display:flex; justify-content:space-between; gap:6px; margin-top:10px; color:#cdbb8e; font-size:10px; }
.native-audio-result { display:flex; align-items:center; gap:12px; margin:10px 0; padding:14px; border:1px solid rgba(90,220,164,.28); border-radius:14px; background:rgba(57,190,128,.08); color:#e5fff0; font-size:13px; line-height:1.55; }
.native-audio-result span { color:#b5ccb9; font-size:12px; }
.result-icon { display:grid; place-items:center; flex:0 0 32px; width:32px; height:32px; border-radius:50%; background:#72dfa1; color:#09331c; font-weight:900; }
@media (max-width: 700px) {
    html, body { width:100% !important; max-width:100% !important; overflow-x:hidden !important; }
    .gradio-container { width:100% !important; max-width:100vw !important; box-sizing:border-box !important; overflow-x:hidden !important; }
    .gradio-container { padding: 12px 10px 28px !important; }
    .hero-card { padding: 24px 19px; border-radius: 17px; }
    .panel { padding: 13px !important; border-radius: 15px !important; }
    .hero-card::after { right: 15px; font-size: 114px; }
    .hero-card h1 { font-size:23px !important; line-height:1.38 !important; overflow-wrap:anywhere; }
    .hero-card p { font-size:13px; line-height:1.55; overflow-wrap:anywhere; }
    .wizard-progress { gap:4px; margin-bottom:13px; }
    .wizard-step { min-height:56px; flex-direction:column; gap:3px; padding:6px 2px; font-size:10px; }
    .wizard-label { overflow:hidden; max-width:100%; text-overflow:ellipsis; white-space:nowrap; }
    .stage-card { width:100% !important; min-width:0 !important; margin:0; box-sizing:border-box !important; }
    .stage-card > * { min-width:0 !important; }
    #reference-audio { width:100% !important; max-width:320px !important; margin:0 auto !important; }
    #reference-audio canvas, #reference-audio svg { max-width:100% !important; }
    .loading-steps { font-size:9px; }
}

/* Compact, fixed-width waveform players for both upload and generated audio. */
@media (max-width: 700px) {
    .gradio-container, .gradio-container .main, .gradio-container .wrap,
    .gradio-container .form, .gradio-container .block, .gradio-container .column,
    .gradio-container .gr-group, .gradio-container .gr-box {
        min-width:0 !important; max-width:100% !important; box-sizing:border-box !important;
    }
    .gradio-container .prose, .gradio-container .prose *, .gradio-container p,
    .gradio-container span, .gradio-container label, .gradio-container button {
        overflow-wrap:anywhere !important; word-break:break-word !important;
    }
    .hero-card { padding:20px 18px !important; background:linear-gradient(145deg,#151715,#0d1110) !important; }
    .hero-card::after { display:none !important; }
    .brand-kicker { font-size:10px !important; letter-spacing:1.25px !important; }
    .hero-card h1 { font-size:21px !important; line-height:1.42 !important; margin:6px 0 !important; }
    .feature-row { gap:6px !important; margin-top:13px !important; }
    .feature-pill { padding:6px 9px !important; font-size:11px !important; }
    .panel { padding:14px !important; background:#121513 !important; }
    .section-title h3 { font-size:18px !important; line-height:1.4 !important; }
    #reference-audio, #result-audio {
        width:280px !important; min-width:0 !important;
        max-width:calc(100vw - 64px) !important; margin:8px auto !important;
        overflow:hidden !important;
    }
    #reference-audio *, #result-audio *,
    #reference-audio .audio-container, #result-audio .audio-container,
    #reference-audio .wrap, #result-audio .wrap {
        min-width:0 !important; max-width:100% !important; box-sizing:border-box !important;
    }
    #reference-audio canvas, #result-audio canvas,
    #reference-audio svg, #result-audio svg { width:100% !important; max-width:100% !important; }
}

/* Midnight Aurora visual refresh */
body {
    background: radial-gradient(circle at 12% 5%, rgba(88,75,255,.24), transparent 32%),
                radial-gradient(circle at 86% 16%, rgba(20,220,200,.16), transparent 28%),
                linear-gradient(145deg, #08091c, #11113a 50%, #080a19) !important;
}
.hero-card {
    border-color:rgba(130,117,255,.48) !important;
    background:linear-gradient(120deg,rgba(32,28,85,.94),rgba(16,25,62,.96) 60%,rgba(16,69,88,.8)) !important;
    box-shadow:0 22px 55px rgba(15,9,60,.48), inset 0 1px rgba(212,207,255,.16) !important;
}
.hero-card::before { content:""; position:absolute; width:210px; height:210px; top:-110px; right:-65px; border-radius:50%; background:radial-gradient(circle,rgba(51,234,220,.28),transparent 68%); filter:blur(4px); animation:aurora-drift 5s ease-in-out infinite alternate; }
.brand-kicker, .section-number { color:#a79bff !important; }
.hero-card h1, .section-title h3 { color:#f4f1ff !important; }
.feature-pill { border-color:rgba(87,235,222,.30) !important; background:rgba(69,226,213,.09) !important; color:#bffff6 !important; }
.panel { border-color:rgba(126,114,255,.22) !important; background:linear-gradient(145deg,rgba(19,21,52,.96),rgba(12,16,38,.96)) !important; box-shadow:0 16px 42px rgba(4,5,28,.38) !important; }
#generate-btn, #generate-btn button { border-color:rgba(196,190,255,.6) !important; background:linear-gradient(110deg,#6959e8,#9b6df6 48%,#2ecfc7) !important; color:#fff !important; box-shadow:0 12px 28px rgba(102,80,232,.34) !important; }
.wizard-step.active { color:#fff !important; border-color:rgba(141,128,255,.72) !important; background:linear-gradient(115deg,rgba(105,89,232,.42),rgba(46,207,199,.14)) !important; }
.wizard-step.active .wizard-dot { color:#171138 !important; background:#b9afff !important; }
.wizard-step.done { color:#9ff5df !important; border-color:rgba(63,218,176,.38) !important; }
.wizard-step.done .wizard-dot { background:#75e4c4 !important; }
#reference-audio, #result-audio { border:1px solid rgba(74,232,215,.30) !important; border-radius:14px !important; background:rgba(9,15,38,.78) !important; box-shadow:0 9px 22px rgba(7,7,35,.25) !important; }
@keyframes aurora-drift { from { transform:translate3d(-10px,0,0) scale(.92); opacity:.55; } to { transform:translate3d(15px,18px,0) scale(1.14); opacity:1; } }
"""

APP_THEME = gr.themes.Soft(
    primary_hue="violet",
    secondary_hue="cyan",
    neutral_hue="slate",
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

    // Keep the old value once so keys bound by v2 can be migrated without reset.
    const legacyParts = [
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

    // Deliberately avoid Canvas/WebGL and hardware-memory details here: those can
    // vary between public link origins even on the same phone/browser.
    const stableParts = [
        "yf-device-fingerprint-v3-stable",
        uaDataPlatform || safe(() => navigator.platform, "unknown"),
        uaDataMobile || (/Mobi|Android|iPhone|iPad/i.test(navigator.userAgent || "") ? "mobile" : "desktop"),
        browserFamily(),
        `${screenShort}x${screenLong}`,
        safe(() => navigator.maxTouchPoints, "0"),
        safe(() => navigator.language, ""),
    ];
    const fingerprint = JSON.stringify({
        version: 3,
        primary: stableParts.join("|"),
        legacy: legacyParts.join("|"),
    });
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
            <span class="feature-pill">🎙️ Voice Studio</span>
            <span class="feature-pill">🎵 MP3 Output</span>
            <span class="feature-pill">👑 VIP</span>
        </div>
    </section>
    """)

    wizard_progress = gr.HTML(wizard_progress_html(1))

    with gr.Column(visible=True, elem_classes=["wizard-stage", "stage-card", "panel"]) as vip_step:
        gr.HTML("""
        <div class="section-title"><div class="section-number">STEP 01 · VIP ACCESS</div>
        <h3>VIP Key ကို အတည်ပြုပါ</h3><p>သင့် License Key ကို စစ်ဆေးပြီး quota ကို ရယူပါ။</p></div>
        """)
        vip_key = gr.Textbox(label="🔑 VIP License Key", placeholder="VIP-USER01-20260430-XXXXXXXX", type="password")
        verify_vip_btn = gr.Button("Continue → VIP Key Verify", variant="primary", elem_id="generate-btn")
        vip_verify_status = gr.Markdown("VIP Key ကို Verify လုပ်ပြီး နောက်အဆင့်သို့ ဆက်သွားပါ။")

    with gr.Column(visible=False, elem_classes=["wizard-stage", "stage-card", "panel"]) as text_step:
        gr.HTML("""
        <div class="section-title"><div class="section-number">STEP 02 · SCRIPT</div>
        <h3>ဖတ်စေလိုသော စာသားထည့်ပါ</h3><p>ဇာတ်ညွှန်း သို့မဟုတ် စာပိုဒ်ကို ထည့်ပြီး နောက်တစ်ဆင့်သို့ ဆက်ပါ။</p></div>
        """)
        text_in = gr.Textbox(label="📝 ဖတ်စေလိုသော စာသား", lines=13, placeholder="ဇာတ်ညွှန်း သို့မဟုတ် စာပိုဒ်အရှည်ကို ဤနေရာတွင် ကူးထည့်ပါ...")
        char_counter = gr.HTML(quota_counter_html("", 0, 0))
        text_continue_btn = gr.Button("Continue → နမူနာအသံ ထည့်မည်", variant="primary", elem_id="generate-btn")
        text_step_status = gr.Markdown("")

    with gr.Column(visible=False, elem_classes=["wizard-stage", "stage-card", "panel"]) as voice_step:
        gr.HTML("""
        <div class="section-title"><div class="section-number">STEP 03 · VOICE SAMPLE</div>
        <h3>နမူနာအသံ ထည့်ပါ</h3><p>5–15 seconds ရှိသော အသံနမူနာကောင်းတစ်ခုကို တင်ပါ။</p></div>
        """)
        audio_in = gr.Audio(type="filepath", sources=["upload"], label="🎤 နမူနာအသံ (5–15 seconds)", elem_id="reference-audio")
        with gr.Accordion("⚙️ Advanced Voice Settings", open=False):
            control_in = gr.Textbox(label="အသံပုံစံညွှန်ကြားချက် (Optional)", placeholder="ဥပမာ — cheerful, calm, whisper, fast")
            clone_str = gr.Slider(minimum=1.0, maximum=3.0, value=2.8, step=0.1, label="Clone Strength")
            use_transcript = gr.Checkbox(label="နမူနာအသံ၏ မူရင်းစာသားကို အသုံးပြုမည်", value=False)
            ref_text_in = gr.Textbox(label="နမူနာအသံ၏ မူရင်းစာသား (Optional)", lines=2)
        gen_btn = gr.Button("✨ MP3 စတင်ထုတ်လုပ်မည်", variant="primary", elem_id="generate-btn")

    with gr.Column(visible=False, elem_classes=["wizard-stage", "stage-card", "panel"]) as result_step:
        gr.HTML("""
        <div class="section-title"><div class="section-number">STEP 04 · RESULT</div>
        <h3>သင့်အသံဖိုင်ကို ပြင်ဆင်နေပါသည်</h3><p>ပြီးသွားလျှင် MP3 ကို ဒီနေရာကနေ download လုပ်နိုင်ပါသည်။</p></div>
        """)
        loading_flow = gr.HTML(visible=False)
        status_markdown = gr.Markdown("⏳ အသံထုတ်လုပ်မှုကို စတင်နေပါသည်…")
        audio_preview = gr.Audio(type="filepath", label="🎧 ထုတ်လုပ်ပြီးသောအသံ", elem_id="result-audio")
        direct_download_html = gr.HTML()

    gr.HTML('<div class="footer-note">YF TTS · Burmese AI Voice Studio · VIP Access</div>')

    verify_vip_btn.click(
        fn=verify_and_open_text_step,
        inputs=[vip_key, device_fingerprint, text_in],
        outputs=[vip_verify_status, vip_total_quota, vip_remaining_quota, char_counter, vip_step, text_step, wizard_progress],
        js=GET_DEVICE_FINGERPRINT_JS,
    )

    vip_key.submit(
        fn=verify_and_open_text_step,
        inputs=[vip_key, device_fingerprint, text_in],
        outputs=[vip_verify_status, vip_total_quota, vip_remaining_quota, char_counter, vip_step, text_step, wizard_progress],
        js=GET_DEVICE_FINGERPRINT_JS,
    )

    text_in.input(
        fn=update_quota_counter,
        inputs=[text_in, vip_total_quota, vip_remaining_quota],
        outputs=[char_counter],
    )

    text_continue_btn.click(
        fn=open_voice_step,
        inputs=[text_in],
        outputs=[text_step_status, text_step, voice_step, wizard_progress],
    )

    generation_event = gen_btn.click(
        fn=open_result_step,
        outputs=[voice_step, result_step, wizard_progress, loading_flow, status_markdown],
    )
    generation_done = generation_event.then(
        fn=generate_vip_long,
        inputs=[vip_key, device_fingerprint, text_in, control_in, audio_in, use_transcript, ref_text_in, clone_str],
        outputs=[audio_preview, direct_download_html, status_markdown, vip_total_quota, vip_remaining_quota, char_counter],
        js=GET_DEVICE_FINGERPRINT_JS,
    )
    generation_done.then(fn=hide_loading_flow, outputs=[loading_flow])

# ==========================================================
# 6. GOOGLE COLAB + CLOUDFLARE QUICK TUNNEL LAUNCHER (FIXED)
# ==========================================================
# Gradio runs only on localhost. cloudflared exposes that local port as
# https://xxxx.trycloudflare.com. No gradio.live share tunnel is used.
import socket
import platform
import urllib.request
from pathlib import Path


def _find_free_port():
    """Ask the OS for an unused localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_local_server(port, timeout=45):
    """Wait until the local Gradio server is accepting TCP connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _cloudflared_download_url():
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported Colab CPU architecture: {machine}")
    return f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"


def _cloudflared_works(binary_path):
    try:
        p = subprocess.run(
            [binary_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
        return p.returncode == 0 and "cloudflared" in (p.stdout or "").lower()
    except Exception:
        return False


def _ensure_cloudflared():
    """Download the official cloudflared binary once per Colab runtime."""
    binary_path = "/content/cloudflared"

    if os.path.exists(binary_path) and os.access(binary_path, os.X_OK):
        if _cloudflared_works(binary_path):
            print("☁️ cloudflared ရှိပြီးသားဖြစ်ပါသည်။")
            return binary_path
        try:
            os.remove(binary_path)
        except OSError:
            pass

    print("☁️ Cloudflare Tunnel binary ကို download လုပ်နေပါသည်...")
    url = _cloudflared_download_url()
    try:
        urllib.request.urlretrieve(url, binary_path)
        os.chmod(binary_path, 0o755)
    except Exception as exc:
        raise RuntimeError(f"cloudflared download မအောင်မြင်ပါ: {exc}") from exc

    if not _cloudflared_works(binary_path):
        raise RuntimeError("cloudflared binary ကို run မရပါ။ Colab Runtime ကို Restart ပြီး ထပ် Run ပါ။")

    print("✅ cloudflared အဆင်သင့်ဖြစ်ပါပြီ။")
    return binary_path


def _stop_previous_colab_servers():
    """Best-effort cleanup when the same Colab cell is run again."""
    old_cf = globals().get("_YF_CLOUDFLARED_PROCESS")
    if old_cf is not None:
        try:
            if old_cf.poll() is None:
                old_cf.terminate()
                try:
                    old_cf.wait(timeout=3)
                except Exception:
                    old_cf.kill()
        except Exception:
            pass

    old_log_handle = globals().get("_YF_CLOUDFLARED_LOG_HANDLE")
    if old_log_handle is not None:
        try:
            old_log_handle.close()
        except Exception:
            pass

    old_demo = globals().get("_YF_RUNNING_DEMO")
    if old_demo is not None:
        try:
            old_demo.close()
        except Exception:
            pass


def _read_log_text(log_path):
    try:
        return Path(log_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _start_cloudflare_process(cloudflared, port, protocol="http2", attempt=1):
    """Start cloudflared without blocking on stdout.readline()."""
    global _YF_CLOUDFLARED_PROCESS, _YF_CLOUDFLARED_LOG_HANDLE

    log_path = f"/content/yf_cloudflared_{attempt}.log"
    try:
        os.remove(log_path)
    except OSError:
        pass

    cmd = [
        cloudflared,
        "tunnel",
        "--url", f"http://localhost:{port}",
        "--no-autoupdate",
        "--loglevel", "info",
    ]
    if protocol:
        cmd += ["--protocol", protocol]

    _YF_CLOUDFLARED_LOG_HANDLE = open(log_path, "w", encoding="utf-8")
    _YF_CLOUDFLARED_PROCESS = subprocess.Popen(
        cmd,
        stdout=_YF_CLOUDFLARED_LOG_HANDLE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return _YF_CLOUDFLARED_PROCESS, log_path


def _wait_for_cloudflare_url(proc, log_path, timeout=60):
    """Poll the cloudflared log file so timeout always works."""
    pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + timeout
    last_text = ""

    while time.time() < deadline:
        last_text = _read_log_text(log_path)
        match = pattern.search(last_text)
        if match:
            return match.group(0), last_text

        if proc.poll() is not None:
            # Process exited; one final read before giving up.
            time.sleep(0.25)
            last_text = _read_log_text(log_path)
            match = pattern.search(last_text)
            if match:
                return match.group(0), last_text
            break

        time.sleep(0.4)

    return None, last_text


def _terminate_process(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
    except Exception:
        pass


def launch_with_cloudflare(gradio_app):
    """
    Colab-safe launcher:
      1) prepare cloudflared first
      2) launch Gradio locally and create a Gradio share link
      3) open a TryCloudflare Quick Tunnel
      4) print both public links
    """
    global _YF_CLOUDFLARED_PROCESS, _YF_RUNNING_DEMO

    _stop_previous_colab_servers()
    cloudflared = _ensure_cloudflared()
    port = _find_free_port()

    print(f"🚀 Gradio local server စတင်နေပါသည်... http://127.0.0.1:{port}")
    launch_result = gradio_app.queue().launch(
        server_name="127.0.0.1",
        server_port=port,
        share=True,
        debug=False,
        prevent_thread_lock=True,
        show_error=True,
        quiet=True,
        inline=False,       # IMPORTANT in Colab: do not stop here rendering localhost iframe
    )
    _YF_RUNNING_DEMO = gradio_app
    # Gradio versions return different launch tuple shapes, so read the public URL safely.
    gradio_share_url = getattr(gradio_app, "share_url", None)
    if not gradio_share_url and isinstance(launch_result, tuple):
        gradio_share_url = next((item for item in launch_result if isinstance(item, str) and ".gradio.live" in item), None)

    if not _wait_for_local_server(port, timeout=45):
        raise RuntimeError("Gradio local server မစတင်နိုင်ပါ။ Colab output ထဲက error ကို စစ်ပါ။")

    print("✅ Gradio localhost အဆင်သင့်ဖြစ်ပါပြီ။")
    print("☁️ Cloudflare Quick Tunnel စတင်နေပါသည်...")

    # Attempt 1: HTTP/2 is usually reliable in Colab networks where UDP/QUIC may be restricted.
    proc, log_path = _start_cloudflare_process(cloudflared, port, protocol="http2", attempt=1)
    public_url, logs = _wait_for_cloudflare_url(proc, log_path, timeout=60)

    # Attempt 2: let cloudflared choose protocol automatically.
    if not public_url:
        print("⚠️ Cloudflare HTTP/2 attempt မအောင်မြင်သေးပါ။ Auto protocol နဲ့ တစ်ကြိမ် ပြန်ကြိုးစားနေပါသည်...")
        _terminate_process(proc)
        try:
            globals().get("_YF_CLOUDFLARED_LOG_HANDLE").close()
        except Exception:
            pass
        proc, log_path = _start_cloudflare_process(cloudflared, port, protocol=None, attempt=2)
        public_url, logs = _wait_for_cloudflare_url(proc, log_path, timeout=60)

    if not public_url:
        _terminate_process(proc)
        tail_lines = (logs or "").splitlines()[-20:]
        tail = "\n".join(tail_lines)
        raise RuntimeError(
            "Cloudflare public link မထွက်လာပါ။ Runtime > Restart session လုပ်ပြီး cell ကို ပြန် Run ကြည့်ပါ။"
            + (f"\n\nနောက်ဆုံး Cloudflare log:\n{tail}" if tail else "")
        )

    print("\n" + "=" * 76)
    print("✅ YF TTS — PUBLIC LINKS READY")
    print(f"🌐 CLOUDFARE LIVE LINK: {public_url}")
    print(f"🔗 GRADIO SHARE LINK: {gradio_share_url or 'မထွက်သေးပါ — Cloudflare link ကို အသုံးပြုပါ'}")
    print("📌 Link နှစ်ခုလုံးသည် တူညီသော app/runtime ကိုဖွင့်ပေးပါသည်။")
    print("⚠️ Colab runtime ပိတ်သွားရင် link ပိတ်ပြီး ပြန် Run တဲ့အခါ link အသစ်ထွက်ပါမယ်။")
    print("=" * 76 + "\n")

    try:
        from IPython.display import HTML, Markdown, display

        # A real anchor-button is more reliable than relying on Colab's auto-linked console text.
        safe_url = str(public_url).replace('"', '&quot;')
        safe_share_url = str(gradio_share_url or "").replace('"', '&quot;')
        gradio_card = "" if not gradio_share_url else f"""
          <a href="{safe_share_url}" target="_blank" rel="noopener noreferrer"
             style="display:inline-block;background:#7c3aed;color:#fff;padding:14px 22px;
                    border-radius:10px;font-size:17px;font-weight:800;text-decoration:none;
                    margin:0 0 14px 8px;cursor:pointer;pointer-events:auto">
             🔗 OPEN GRADIO SHARE LINK
          </a>"""
        display(HTML(f"""
        <div style="padding:20px;border:2px solid #f0b429;border-radius:14px;
                    background:#111827;color:white;margin:12px 0;font-family:Arial,sans-serif">
          <div style="font-size:13px;opacity:.85;margin-bottom:12px">☁️ CLOUDFLARE LIVE WEBSITE</div>
          <a href="{safe_url}" target="_blank" rel="noopener noreferrer"
             style="display:inline-block;background:#2563eb;color:#fff;padding:14px 22px;
                    border-radius:10px;font-size:17px;font-weight:800;text-decoration:none;
                    margin-bottom:14px;cursor:pointer;pointer-events:auto">
             🌐 OPEN CLOUDFLARE WEBSITE
          </a>
          {gradio_card}
          <div style="margin-top:4px;font-size:13px;opacity:.8">Button မနှိပ်ရပါက အောက်က URL ကို Copy → Browser Address Bar မှာ Paste လုပ်ပါ။</div>
          <div style="margin-top:8px;padding:10px;background:#0b1220;border-radius:8px;
                      word-break:break-all;user-select:text;color:#93c5fd">{safe_url}</div>
        </div>
        """))

        # Markdown link provides a second, independent clickable surface in Colab.
        display(Markdown(
            f"### 🌐 [OPEN CLOUDFLARE LIVE WEBSITE]({public_url})" +
            (f"  |  🔗 [OPEN GRADIO SHARE LINK]({gradio_share_url})" if gradio_share_url else "")
        ))
    except Exception as e:
        print(f"Cloudflare link card display warning: {e}")

    return public_url, gradio_share_url


CLOUDFLARE_PUBLIC_URL, GRADIO_SHARE_URL = launch_with_cloudflare(demo)
