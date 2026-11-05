# Translator

A Windows desktop translator that looks and works like Google Translate. It's built with Python and PySide6 (Qt).

## Features
- Detects the source language automatically, and supports 130+ languages
- Translates as you type
- Swap button: swaps the languages and puts the translation in the input box
- Romanization (for example, こんにちは → Kon'nichiwa)
- "Did you mean" spelling suggestions
- Dictionary panel for single words. Click a word to translate it.
- Reads text aloud (🔊) and copies the translation (⧉)
- 5,000-character limit, like Google
- Remembers your languages and window size

Keyboard shortcuts: `Ctrl+Enter` translate now · `Ctrl+Shift+S` swap · `Ctrl+Shift+C` copy · `Esc` clear

## Run from source
```
pip install -r requirements.txt
python main.py
```

## Build the .exe (on Windows)
Double-click `build.bat`. It creates `dist\Translator.exe`, a single file that needs no Python to run.

## Note
The app uses Google Translate's free web endpoint, so you don't need an API key. The endpoint is unofficial: Google may rate-limit heavy use, and it could change without notice.
