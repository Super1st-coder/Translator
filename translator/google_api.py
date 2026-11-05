"""Client for Google Translate's free web endpoints (no API key needed).

These are the same endpoints the Google Translate browser widget uses. They are
unofficial, so Google may rate-limit heavy use.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .languages import AUTO, normalize

TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
TTS_URL = "https://translate.google.com/translate_tts"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TTS_CHUNK = 200  # the TTS endpoint rejects longer text


class TranslationError(Exception):
    pass


@dataclass
class DictionaryEntry:
    part_of_speech: str
    words: list[tuple[str, list[str]]]  # (translation, reverse translations)


@dataclass
class Translation:
    text: str
    source_lang: str
    target_lang: str
    source_romanization: str = ""
    target_romanization: str = ""
    did_you_mean: str = ""
    dictionary: list[DictionaryEntry] = field(default_factory=list)


def _request(url: str, data: bytes | None = None, timeout: float = 10) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise TranslationError("Too many requests. Please wait a moment and try again.")
        raise TranslationError(f"Google returned HTTP {e.code}.")
    except (urllib.error.URLError, TimeoutError) as e:
        raise TranslationError("Could not reach Google Translate. Check your internet connection.") from e


def translate(text: str, source: str = AUTO, target: str = "en") -> Translation:
    params = [
        ("client", "gtx"), ("sl", source), ("tl", target), ("hl", "en"),
        ("dj", "1"), ("ie", "UTF-8"), ("oe", "UTF-8"),
        ("dt", "t"),    # translation
        ("dt", "rm"),   # romanization
        ("dt", "bd"),   # dictionary
        ("dt", "qca"),  # spelling correction
    ]
    url = TRANSLATE_URL + "?" + urllib.parse.urlencode(params)
    # POST the text so long inputs don't overflow the URL length limit.
    body = urllib.parse.urlencode({"q": text}).encode()
    try:
        data = json.loads(_request(url, body))
    except json.JSONDecodeError as e:
        raise TranslationError("Unexpected response from Google Translate.") from e

    result = Translation(text="", source_lang=normalize(data.get("src", source)), target_lang=target)
    parts = []
    for sentence in data.get("sentences", []):
        if "trans" in sentence:
            parts.append(sentence["trans"])
        else:
            result.target_romanization = sentence.get("translit", "") or ""
            result.source_romanization = sentence.get("src_translit", "") or ""
    result.text = "".join(parts)

    spell = data.get("spell", {}).get("spell_res", "")
    if spell and spell.strip() != text.strip():
        result.did_you_mean = spell

    for entry in data.get("dict", []):
        words = [(e.get("word", ""), e.get("reverse_translation", [])) for e in entry.get("entry", [])]
        if words:
            result.dictionary.append(DictionaryEntry(entry.get("pos", ""), words))
    return result


def _chunks(text: str, size: int = TTS_CHUNK) -> list[str]:
    """Split text into pieces of at most `size` chars, preferring sentence/word breaks."""
    text = " ".join(text.split())
    chunks = []
    while len(text) > size:
        window = text[:size]
        cut = max(window.rfind(p) for p in (". ", "! ", "? ", "。", ", ", "、"))
        if cut <= 0:
            cut = window.rfind(" ")
        cut = cut + 1 if cut > 0 else size
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


def speech(text: str, lang: str) -> bytes:
    """Return MP3 audio of `text` spoken in `lang`."""
    audio = b""
    pieces = _chunks(text)
    for i, piece in enumerate(pieces):
        params = {
            "ie": "UTF-8", "client": "tw-ob", "tl": lang, "q": piece,
            "total": len(pieces), "idx": i, "textlen": len(piece),
        }
        # MP3 frames can be concatenated directly.
        audio += _request(TTS_URL + "?" + urllib.parse.urlencode(params))
    if not audio:
        raise TranslationError("Nothing to read aloud.")
    return audio
