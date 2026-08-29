# @title ✨ YF TTS — Burmese AI Voice Studio ( Friendly) { display-mode: "form" }
# @markdown Play ခလုတ်ကို နှိပ်ပြီး App ကို စတင်အသုံးပြုနိုင်ပါသည်။

# ==========================================================
# 1. INSTALL PACKAGES
# ==========================================================
import subprocess
import sys
import shutil

packages = [
    "voxcpm",
    "soundfile",
    "gradio",
    "torch",
    "numpy",
    "pydub",
    "pymongo",
    "dnspython",
]

subprocess.check_call(
    [sys.executable, "-m", "pip", "install", "-q", *packages]
)

# MP3 export အတွက် ffmpeg မရှိသေးရင် install လုပ်မည်
if shutil.which("ffmpeg") is None:
    subprocess.run(
        ["apt-get", "update", "-qq"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        ["apt-get", "install", "-y", "-qq", "ffmpeg"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

# ==========================================================
# 2. IMPORTS
# ==========================================================
import os
import gc
import re
import time
import datetime
import traceback

import torch
import numpy as np
import soundfile as sf
import gradio as gr

from pydub import AudioSegment
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from voxcpm import VoxCPM


# ==========================================================
# 3. SECURE SECRET LOADER
# ==========================================================
# Google Colab Secrets ထဲမှာ:
# Name  : MONGODB_URI
# Value : mongodb+srv://...
#
# Local / Server မှာ environment variable MONGODB_URI သုံးနိုင်သည်။

def get_secret(name: str):
    value = os.getenv(name)
    if value:
        return value

    try:
        from google.colab import userdata
        value = userdata.get(name)
        if value:
            return value
    except Exception:
        pass

    return None


MONGODB_URI = get_secret("MONGODB_URI")
DB_NAME = "vip_portal"
COL_NAME = "vip_licenses"


def get_secure_client():
    if not MONGODB_URI:
        raise RuntimeError(
            "MONGODB_URI မတွေ့ပါ။ Google Colab > Secrets ထဲတွင် MONGODB_URI ထည့်ပေးပါ။"
        )

    return MongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=7000,
        appname="YF-TTS-User-Client",
    )


def verify_vip_license(key_str):
    if not key_str or not key_str.strip():
        return False, "❌ VIP License Key ထည့်သွင်းပေးပါ။"

    clean_key = key_str.strip()
    client = None

    try:
        client = get_secure_client()
        collection = client[DB_NAME][COL_NAME]

        record = collection.find_one({"vip_key": clean_key})

        if not record:
            return (
                False,
                "❌ ဤ VIP Key သည် စနစ်ထဲတွင် မရှိပါ၊ ပယ်ဖျက်ထားခြင်း သို့မဟုတ် မမှန်ကန်ခြင်း ဖြစ်နိုင်ပါသည်။",
            )

        if record.get("status") != "active":
            return False, "❌ ဤ VIP Key သည် အသုံးပြုခွင့် ပိတ်ထားပါသည်။"

        expires_at = record.get("expires_at")
        now = datetime.datetime.now(datetime.timezone.utc)

        if not expires_at:
            return False, "❌ VIP သက်တမ်းအချက်အလက် မမှန်ကန်ပါ။"

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)

        if now > expires_at:
            return (
                False,
                f"❌ VIP သက်တမ်းသည် {expires_at.strftime('%Y-%m-%d')} တွင် ကုန်ဆုံးသွားပါပြီ။",
            )

        days_left = max(0, (expires_at.date() - now.date()).days)
        user_name = record.get("user_name", "VIP Member")

        return (
            True,
            f"👑 VIP Access အတည်ပြုပြီးပါပြီ — {user_name} · သက်တမ်းကျန် {days_left} ရက်",
        )

    except PyMongoError:
        return (
            False,
            "❌ Database ချိတ်ဆက်၍ မရပါ။ Internet နှင့် MongoDB connection ကို စစ်ဆေးပါ။",
        )
    except Exception as e:
        return False, f"❌ VIP စစ်ဆေးမှု မအောင်မြင်ပါ — {str(e)}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


# ==========================================================
# 4. LOAD VOXCPM2 MODEL
# ==========================================================
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

print(f"🚀 Running on Device: {DEVICE.upper()}")
print("⏳ VoxCPM2 Model ကို စတင်ဆွဲတင်နေပါသည်...")

model = VoxCPM.from_pretrained(
    "openbmb/VoxCPM2",
    load_denoiser=False,
)

print("✅ VoxCPM2 Model Loaded Successfully!")


# ==========================================================
# 5. TEXT CHUNKING
# ==========================================================
def split_burmese_text_long(text, max_chars=90):
    """
    Burmese punctuation / line break အလိုက် စာကို အပိုင်းခွဲသည်။
    စာပိုဒ်ရှည်လွန်းပါက space အလိုက် ထပ်ခွဲသည်။
    """
    text = (text or "").strip()

    if not text:
        return []

    raw_sentences = re.split(r"([။၊\n?!])", text)
    chunks = []
    current = ""

    for item in raw_sentences:
        current += item

        if item in ["။", "၊", "\n", "?", "!"]:
            current = current.strip()

            if current:
                if len(current) <= max_chars:
                    chunks.append(current)
                else:
                    words = current.split()
                    sub = ""

                    for word in words:
                        candidate = f"{sub} {word}".strip()

                        if len(candidate) <= max_chars:
                            sub = candidate
                        else:
                            if sub:
                                chunks.append(sub)
                            sub = word

                    if sub:
                        chunks.append(sub)

            current = ""

    if current.strip():
        tail = current.strip()

        if len(tail) <= max_chars:
            chunks.append(tail)
        else:
            words = tail.split()
            sub = ""

            for word in words:
                candidate = f"{sub} {word}".strip()

                if len(candidate) <= max_chars:
                    sub = candidate
                else:
                    if sub:
                        chunks.append(sub)
                    sub = word

            if sub:
                chunks.append(sub)

    return [chunk for chunk in chunks if chunk.strip()]


# ==========================================================
# 6. AUDIO HELPERS
# ==========================================================
OUTPUT_DIR = "yf_tts_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def normalize_wav(wav):
    """
    Model output ကို mono float32 numpy array အဖြစ် ပြောင်းပေးသည်။
    """
    if isinstance(wav, torch.Tensor):
        wav = wav.detach().float().cpu().numpy()

    wav = np.asarray(wav, dtype=np.float32)

    if wav.ndim > 1:
        wav = np.squeeze(wav)

    return wav


def safe_instruction(instruction):
    instruction = (instruction or "").strip()
    return instruction[:200]


# ==========================================================
# 7. MP3-ONLY LONG TEXT GENERATION
# ==========================================================
def generate_vip_long(
    vip_key,
    text,
    control_instruction,
    reference_audio,
    use_reference_transcript,
    reference_text,
    clone_strength,
    progress=gr.Progress(),
):
    # ---------- VIP Check ----------
    is_valid, auth_msg = verify_vip_license(vip_key)

    if not is_valid:
        return None, None, auth_msg

    # ---------- Input Validation ----------
    if not text or not text.strip():
        return None, None, "❌ ဖတ်စေလိုသော စာသား ထည့်ပေးပါ။"

    if not reference_audio:
        return None, None, "❌ နမူနာအသံဖိုင် ထည့်ပေးပါ။"

    # ---------- Prepare ----------
    chunks = split_burmese_text_long(text.strip(), max_chars=90)

    if not chunks:
        chunks = [text.strip()]

    prompt_text = None
    if use_reference_transcript and reference_text and reference_text.strip():
        prompt_text = reference_text.strip()

    instruction = safe_instruction(control_instruction)
    clone_strength = float(clone_strength)

    audio_segments = []
    sample_rate = int(model.tts_model.sample_rate)

    # စာကြောင်းအကြား နားချိန်
    silence_gap = 0.15
    silence_samples = int(sample_rate * silence_gap)

    total = len(chunks)
    start_all = time.time()
    success_chunks = 0
    failed_chunks = 0

    # ---------- Generate Each Chunk ----------
    for idx, chunk in enumerate(chunks):
        pct = idx / max(total, 1)
        elapsed = time.time() - start_all

        if idx > 0:
            estimated_total = (elapsed / idx) * total
            remaining = max(0, int(estimated_total - elapsed))
        else:
            remaining = 0

        rem_min = remaining // 60
        rem_sec = remaining % 60

        progress(
            pct,
            desc=(
                f"🎙️ စာပိုင်း {idx + 1}/{total} ထုတ်လုပ်နေပါသည်..."
                + (
                    f" · ခန့်မှန်းကျန် {rem_min} မိနစ် {rem_sec} စက္ကန့်"
                    if idx > 0
                    else ""
                )
            ),
        )

        full_chunk_text = (
            f"({instruction}) {chunk}"
            if instruction
            else chunk
        )

        try:
            # Transcript mode ကို ပထမ chunk တွင်သာ သုံးထားသည်
            if prompt_text and idx == 0:
                wav = model.generate(
                    text=full_chunk_text,
                    prompt_wav_path=reference_audio,
                    prompt_text=prompt_text,
                    reference_wav_path=reference_audio,
                    cfg_value=clone_strength,
                )
            else:
                wav = model.generate(
                    text=full_chunk_text,
                    reference_wav_path=reference_audio,
                    cfg_value=clone_strength,
                )

            wav = normalize_wav(wav)

            if wav.size == 0:
                raise RuntimeError("Model မှ empty audio ပြန်လာပါသည်။")

            audio_segments.append(wav)
            audio_segments.append(
                np.zeros(silence_samples, dtype=np.float32)
            )

            success_chunks += 1

        except Exception as e:
            failed_chunks += 1
            print(f"❌ Chunk #{idx + 1} Error: {e}")
            traceback.print_exc()

        # GPU memory cleanup
        if (idx + 1) % 8 == 0:
            gc.collect()

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    # ---------- Final Check ----------
    if not audio_segments or success_chunks == 0:
        return (
            None,
            None,
            "❌ အသံထုတ်လုပ်ခြင်း မအောင်မြင်ပါ။ Reference audio သို့မဟုတ် စာသားကို စစ်ဆေးပါ။",
        )

    progress(0.96, desc="🎧 Audio ကို ပေါင်းစည်းပြီး MP3 ပြောင်းနေပါသည်...")

    # နောက်ဆုံး silence တစ်ခုကို ဖြုတ်မည်
    if len(audio_segments) >= 2:
        final_wav = np.concatenate(audio_segments[:-1])
    else:
        final_wav = np.concatenate(audio_segments)

    # clipping ကာကွယ်ရန်
    peak = float(np.max(np.abs(final_wav))) if final_wav.size else 0.0
    if peak > 1.0:
        final_wav = final_wav / peak

    timestamp = int(time.time() * 1000)

    temp_wav_path = os.path.join(
        OUTPUT_DIR,
        f"yf_temp_{timestamp}.wav",
    )

    output_mp3_path = os.path.join(
        OUTPUT_DIR,
        f"YF_Cloned_Voice_{timestamp}.mp3",
    )

    try:
        sf.write(
            temp_wav_path,
            final_wav,
            sample_rate,
        )

        audio_segment = AudioSegment.from_wav(temp_wav_path)

        audio_segment.export(
            output_mp3_path,
            format="mp3",
            bitrate="192k",
        )

    except Exception as e:
        return None, None, f"❌ MP3 ဖိုင်ပြောင်းရာတွင် error ဖြစ်ပါသည် — {str(e)}"

    finally:
        if os.path.exists(temp_wav_path):
            try:
                os.remove(temp_wav_path)
            except Exception:
                pass

    # ---------- Status ----------
    duration_sec = len(final_wav) / sample_rate
    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)

    if failed_chunks:
        chunk_info = (
            f"\n⚠️ **အောင်မြင်:** {success_chunks}/{total} စာပိုင်း"
            f" · **မအောင်မြင်:** {failed_chunks}"
        )
    else:
        chunk_info = f"\n✅ **စာပိုင်း:** {success_chunks}/{total} အပြည့်အစုံ အောင်မြင်"

    status_text = (
        f"{auth_msg}\n\n"
        f"🎉 **MP3 အသံဖိုင် ထုတ်လုပ်ပြီးပါပြီ။**"
        f"{chunk_info}\n"
        f"⏱️ **အသံကြာချိန်:** {mins} မိနစ် {secs} စက္ကန့်\n"
        f"🎧 အောက်တွင် နားဆင်နိုင်ပြီး **MP3 Download** မှ ဖိုင်ယူနိုင်ပါသည်။"
    )

    progress(1.0, desc="✅ ပြီးပါပြီ")

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return output_mp3_path, output_mp3_path, status_text


# ==========================================================
# 8. MOBILE-FIRST UI CSS
# ==========================================================
APP_CSS = r"""
:root {
    --yf-gold: #e8b84f;
    --yf-gold-2: #f5d27b;
    --yf-dark: #080808;
    --yf-panel: #141310;
    --yf-border: rgba(238, 195, 90, 0.22);
    --yf-text: #fff8e7;
    --yf-muted: #aaa18f;
}

html, body {
    overflow-x: hidden !important;
}

body {
    background:
        radial-gradient(circle at 8% 3%, rgba(255, 198, 76, .13), transparent 31%),
        radial-gradient(circle at 92% 10%, rgba(174, 125, 28, .12), transparent 28%),
        linear-gradient(150deg, #070707, #11100c 55%, #090909);
    color: var(--yf-text);
}

.gradio-container {
    width: 100% !important;
    max-width: 1180px !important;
    margin: 0 auto !important;
    padding: 22px 16px 38px !important;
}

.hero-card {
    position: relative;
    overflow: hidden;
    padding: 30px 30px 27px;
    border: 1px solid rgba(235, 190, 82, .37);
    border-radius: 24px;
    background:
        linear-gradient(
            118deg,
            rgba(36, 29, 12, .96),
            rgba(17, 16, 13, .96) 58%,
            rgba(57, 40, 11, .78)
        );
    box-shadow:
        0 22px 60px rgba(0, 0, 0, .42),
        inset 0 1px 0 rgba(255, 230, 169, .10);
    margin-bottom: 17px;
}

.hero-card::after {
    content: "YF";
    position: absolute;
    right: 28px;
    top: -42px;
    color: rgba(255, 212, 118, .06);
    font-size: 158px;
    font-weight: 800;
    letter-spacing: -15px;
    pointer-events: none;
}

.brand-kicker {
    position: relative;
    z-index: 2;
    color: #e4b650;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
}

.hero-card h1 {
    position: relative;
    z-index: 2;
    margin: 7px 0 8px;
    font-size: clamp(27px, 4vw, 43px);
    line-height: 1.2;
    color: #fff9e9 !important;
}

.hero-card p {
    position: relative;
    z-index: 2;
    max-width: 760px;
    margin: 0;
    color: #d5cdbc;
    line-height: 1.7;
    font-size: 14px;
}

.feature-row {
    position: relative;
    z-index: 2;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 16px;
}

.feature-pill {
    padding: 7px 11px;
    border: 1px solid rgba(238, 195, 90, .27);
    border-radius: 999px;
    background: rgba(242, 190, 66, .08);
    color: #f4d890;
    font-size: 12px;
    white-space: nowrap;
}

.mobile-tip {
    margin: 0 0 16px;
    padding: 10px 13px;
    border: 1px solid rgba(232, 184, 79, .18);
    border-radius: 12px;
    background: rgba(232, 184, 79, .06);
    color: #d7c9a8;
    font-size: 12px;
    line-height: 1.55;
}

.main-grid {
    gap: 16px !important;
    align-items: stretch !important;
}

.panel {
    min-width: 0 !important;
    border: 1px solid var(--yf-border) !important;
    border-radius: 20px !important;
    padding: 18px !important;
    background:
        linear-gradient(
            145deg,
            rgba(30, 29, 25, .93),
            rgba(16, 16, 15, .95)
        ) !important;
    box-shadow: 0 16px 42px rgba(0, 0, 0, .27);
}

.section-number {
    color: #e5b44c;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.4px;
}

.section-title h3 {
    margin: 3px 0 2px;
    color: #fff7e2 !important;
    font-size: 19px;
    font-weight: 700;
}

.section-title p {
    margin: 0 0 13px;
    color: #aaa18f;
    font-size: 13px;
    line-height: 1.55;
}

#generate-btn,
#generate-btn button {
    width: 100% !important;
    min-height: 54px;
    border: 1px solid rgba(255, 235, 182, .42) !important;
    border-radius: 13px !important;
    background:
        linear-gradient(
            105deg,
            #bb7b13,
            #f0c55d 52%,
            #bb7b13
        ) !important;
    color: #1b1203 !important;
    box-shadow: 0 12px 28px rgba(193, 130, 16, .26);
    font-size: 16px !important;
    font-weight: 800 !important;
    transition: .2s ease;
}

#generate-btn:hover,
#generate-btn button:hover {
    transform: translateY(-1px);
    filter: brightness(1.06);
}

.footer-note {
    text-align: center;
    color: #8f856f;
    font-size: 11px;
    margin-top: 17px;
    padding: 0 8px;
}

/* Textbox / Audio / File components */
textarea {
    font-size: 16px !important;
    line-height: 1.55 !important;
}

input {
    font-size: 16px !important;
}

/* Mobile */
@media (max-width: 760px) {
    .gradio-container {
        padding: 10px 9px 24px !important;
    }

    .hero-card {
        padding: 22px 17px 20px;
        border-radius: 17px;
        margin-bottom: 11px;
    }

    .hero-card::after {
        right: 13px;
        top: -22px;
        font-size: 100px;
        letter-spacing: -10px;
    }

    .brand-kicker {
        font-size: 10px;
        letter-spacing: 1.3px;
    }

    .hero-card h1 {
        margin-top: 7px;
        font-size: 25px;
        line-height: 1.25;
        max-width: 92%;
    }

    .hero-card p {
        font-size: 13px;
        line-height: 1.6;
    }

    .feature-row {
        gap: 6px;
        margin-top: 13px;
    }

    .feature-pill {
        padding: 6px 9px;
        font-size: 11px;
    }

    .main-grid {
        display: block !important;
    }

    .main-grid > div {
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        margin-bottom: 12px !important;
    }

    .panel {
        width: 100% !important;
        padding: 13px !important;
        border-radius: 15px !important;
        box-sizing: border-box !important;
    }

    .section-title h3 {
        font-size: 18px;
    }

    .section-title p {
        font-size: 12px;
    }

    #generate-btn,
    #generate-btn button {
        min-height: 56px !important;
        font-size: 15px !important;
    }

    textarea {
        min-height: 170px !important;
    }

    .footer-note {
        margin-top: 10px;
    }
}

/* Very small phones */
@media (max-width: 390px) {
    .gradio-container {
        padding-left: 7px !important;
        padding-right: 7px !important;
    }

    .hero-card {
        padding-left: 14px;
        padding-right: 14px;
    }

    .hero-card h1 {
        font-size: 22px;
    }

    .panel {
        padding: 11px !important;
    }

    .feature-pill {
        font-size: 10px;
    }
}
"""


# ==========================================================
# 9. GRADIO THEME
# ==========================================================
APP_THEME = gr.themes.Soft(
    primary_hue="amber",
    secondary_hue="yellow",
    neutral_hue="stone",
)


# ==========================================================
# 10. GRADIO APP
# ==========================================================
with gr.Blocks(
    title="YF TTS · Burmese AI Voice Studio",
    theme=APP_THEME,
    css=APP_CSS,
) as demo:

    gr.HTML(
        """
        <section class="hero-card">
            <div class="brand-kicker">
                YF TTS · Burmese AI Voice Studio
            </div>

            <h1>🎙️ သင့်အသံဖြင့် စာသားတိုင်းကို အသက်သွင်းပါ</h1>

            <p>
                စာမျက်နှာရှည် ဇာတ်ညွှန်းများ၊ narration များနှင့်
                စာအုပ်စာသားများကို နမူနာအသံဖြင့်
                MP3 အသံဖိုင်အဖြစ် ထုတ်လုပ်နိုင်ပါသည်။
            </p>

            <div class="feature-row">
                <span class="feature-pill">⚡ GPU Powered</span>
                <span class="feature-pill">🎧 Voice Cloning</span>
                <span class="feature-pill">🎵 MP3 Only</span>
                <span class="feature-pill">📱 Mobile Friendly</span>
                <span class="feature-pill">👑 VIP Access</span>
            </div>
        </section>
        """
    )

    gr.HTML(
        """
        <div class="mobile-tip">
            📱 <b>Mobile အသုံးပြုသူများအတွက်:</b>
            Input နှင့် Result panel များကို အပေါ်အောက် စီပေးထားပြီး
            Download ကို MP3 ဖိုင်အဖြစ် တိုက်ရိုက်ယူနိုင်ပါသည်။
        </div>
        """
    )

    with gr.Row(
        equal_height=False,
        elem_classes="main-grid",
    ):

        # ---------------- INPUT PANEL ----------------
        with gr.Column(
            scale=6,
            min_width=300,
            elem_classes="panel",
        ):
            gr.HTML(
                """
                <div class="section-title">
                    <div class="section-number">STEP 01</div>
                    <h3>အသံထုတ်လုပ်ရန်</h3>
                    <p>
                        VIP Key၊ ဖတ်စေလိုသောစာနှင့်
                        နမူနာအသံကို ထည့်ပါ။
                    </p>
                </div>
                """
            )

            vip_key = gr.Textbox(
                label="🔑 VIP License Key",
                placeholder="VIP-USER01-20260430-XXXXXXXX",
                type="password",
            )

            text_in = gr.Textbox(
                label="📝 ဖတ်စေလိုသော စာသား",
                lines=11,
                max_lines=20,
                placeholder=(
                    "ဇာတ်ညွှန်း၊ narration သို့မဟုတ် "
                    "စာပိုဒ်အရှည်ကို ဒီနေရာတွင် ကူးထည့်ပါ..."
                ),
            )

            audio_in = gr.Audio(
                type="filepath",
                label="🎤 နမူနာအသံ (အကြံပြု 5–15 seconds)",
            )

            with gr.Accordion(
                "⚙️ Advanced Voice Settings",
                open=False,
            ):
                control_in = gr.Textbox(
                    label="အသံပုံစံညွှန်ကြားချက် (Optional)",
                    placeholder=(
                        "ဥပမာ — calm, cheerful, serious, "
                        "soft, storytelling"
                    ),
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
                    placeholder=(
                        "နမူနာအသံထဲတွင် ပြောထားသော "
                        "စာသားကို တိတိကျကျ ထည့်ပါ..."
                    ),
                )

            gen_btn = gr.Button(
                "✨ MP3 အသံ စတင်ထုတ်လုပ်မည်",
                variant="primary",
                elem_id="generate-btn",
            )

        # ---------------- RESULT PANEL ----------------
        with gr.Column(
            scale=5,
            min_width=300,
            elem_classes="panel",
        ):
            gr.HTML(
                """
                <div class="section-title">
                    <div class="section-number">STEP 02</div>
                    <h3>ရလဒ်</h3>
                    <p>
                        လုပ်ဆောင်မှုအခြေအနေ၊ အသံ preview နှင့်
                        MP3 download ကို ဒီနေရာတွင် ရယူပါ။
                    </p>
                </div>
                """
            )

            status_markdown = gr.Markdown(
                "✅ အသံထုတ်လုပ်ရန် အဆင်သင့်ဖြစ်ပါပြီ။"
            )

            audio_preview = gr.Audio(
                label="🎧 အသံရလဒ်ကို နားဆင်ရန်",
                type="filepath",
            )

            mp3_download = gr.File(
                label="📥 MP3 Download",
                file_count="single",
            )

    gr.HTML(
        """
        <div class="footer-note">
            YF TTS · Burmese AI Voice Studio · MP3 Voice Generation · VIP Access
        </div>
        """
    )

    # ======================================================
    # 11. BUTTON EVENT
    # ======================================================
    gen_btn.click(
        fn=generate_vip_long,
        inputs=[
            vip_key,
            text_in,
            control_in,
            audio_in,
            use_transcript,
            ref_text_in,
            clone_str,
        ],
        outputs=[
            audio_preview,
            mp3_download,
            status_markdown,
        ],
    )


# ==========================================================
# 12. LAUNCH
# ==========================================================
demo.queue(
    default_concurrency_limit=1,
).launch(
    share=True,
    debug=True,
)
