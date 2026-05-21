#!/usr/bin/env python3
"""
Transcriber
YouTube Transcript Downloader & Summarizer
==========================================

Downloads transcripts from YouTube videos and optionally summarizes them using Claude AI.

Features:
- 📥 Downloads transcript/subtitles from any YouTube video
- 📝 Extracts video metadata (title, channel, upload date, description)
- 🤖 Summarizes the transcript using Claude AI (optional)
- 💾 Saves both transcript and summary as text files

Usage:
    python transcriber.py [URL] [--summarize]

Examples:
    python transcriber.py
    python transcriber.py https://www.youtube.com/watch?v=dQw4w9WgXcQ
    python transcriber.py https://www.youtube.com/watch?v=dQw4w9WgXcQ --summarize

Requirements:
    pip install youtube_transcript_api anthropic python-dotenv
"""

# =============================================================================
# IMPORTS - Standardbibliotek (inbyggda i Python)
# =============================================================================
import re              # Regular expressions - för att söka efter mönster i text (t.ex. video-ID)
import datetime        # Datum/tid - för att skapa tidsstämplar i filnamn
import urllib.request  # HTTP-förfrågningar - för att hämta YouTube-sidan
import urllib.error    # URL-fel - för att fånga nätverksfel
import html            # HTML-entiteter - för att avkoda &amp; etc. i transkript
import xml.etree.ElementTree as ET  # XML-parsning - för att läsa transkript-XML
import os              # Operativsystem - för miljövariabler (API-nycklar)
import sys             # System - för att avsluta programmet vid fel (sys.exit)
import argparse        # Argumentparser - för att hantera kommandoradsargument (--summarize etc)
from pathlib import Path  # Sökvägshantering - modern och plattformsoberoende filhantering
from typing import Optional

# =============================================================================
# IMPORTS - Externa paket (måste installeras med pip)
# =============================================================================

# pip install python-dotenv youtube_transcript_api anthropic

# python-dotenv: Laddar miljövariabler från .env-filer
# Används för att säkert lagra API-nycklar utanför koden
try:
    from dotenv import load_dotenv
except ImportError:
    print("❌ Missing package: python-dotenv")
    print("   Install with: pip install python-dotenv")
    sys.exit(1)


# =============================================================================
# FUNKTION: extract_video_id
# =============================================================================
# Extraherar det 11-tecken långa video-ID:t från en YouTube-URL.
# 
# YouTube video-ID:n är alltid exakt 11 tecken och kan innehålla:
# - Bokstäver (a-z, A-Z)
# - Siffror (0-9)
# - Understreck (_) och bindestreck (-)
#
# Exempel på URL:er som fungerar:
# - https://www.youtube.com/watch?v=dQw4w9WgXcQ
# - https://youtu.be/dQw4w9WgXcQ
# - https://www.youtube.com/embed/dQw4w9WgXcQ
# =============================================================================
def extract_video_id(url: str) -> str:
    """Extract the 11-character video ID from a YouTube URL."""
    # Regex-mönstret [a-zA-Z0-9_-]{11} matchar exakt 11 tecken
    vid_match = re.search(r"([a-zA-Z0-9_-]{11})", url)
    if not vid_match:
        raise ValueError("Could not extract video ID from URL")
    return vid_match.group(1)  # Returnera den matchade texten


# =============================================================================
# FUNKTION: fetch_video_metadata
# =============================================================================
# Hämtar metadata om videon genom att ladda ner YouTube-sidans HTML.
# 
# Detta är ett "web scraping"-approach - vi läser själva webbsidan och
# extraherar information med regex. YouTube har ingen enkel publik API
# för att hämta denna info utan autentisering.
#
# Returnerar en dictionary med:
# - title_original: Originaltitel (kan innehålla specialtecken)
# - title_clean: Rensad titel för filnamn
# - channel_original: Kanalnamn
# - channel_clean: Rensat kanalnamn för filnamn
# - upload_date: Uppladdningsdatum (YYYY-MM-DD)
# - description: Videobeskrivning
# =============================================================================
def fetch_video_metadata(vid: str) -> dict:
    """Fetch video metadata from YouTube page HTML."""
    # Hämta hela HTML-sidan för videon
    req = urllib.request.Request(
        f"https://www.youtube.com/watch?v={vid}",
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    )
    html = urllib.request.urlopen(req).read().decode()
    
    # --- TITEL ---
    # Söker efter <title>Videotitel - YouTube</title> i HTML:en
    title_match = re.search(r"<title>(.+?) - YouTube</title>", html)
    title_original = title_match.group(1) if title_match else "Unknown Title"
    # Rensa titeln för filnamn: behåll endast bokstäver, siffror, svenska tecken och mellanslag
    # [:50] begränsar längden till 50 tecken (för att undvika för långa filnamn)
    title_clean = re.sub(r"[^a-zA-Z0-9åäöÅÄÖ ]", "", title_original).replace(" ", "_")[:50]
    
    # --- KANAL ---
    # Söker efter <link itemprop="name" content="Kanalnamn"> i HTML:en
    channel_match = re.search(r'link itemprop="name" content="([^"]+)"', html)
    channel_original = channel_match.group(1) if channel_match else "Unknown Channel"
    channel_clean = re.sub(r"[^a-zA-Z0-9åäöÅÄÖ ]", "", channel_original).replace(" ", "_")[:30]
    
    # --- UPPLADDNINGSDATUM ---
    # Söker efter "uploadDate":"2024-01-15" i JSON-datan som finns i HTML:en
    upload_date_match = re.search(r'"uploadDate":"(\d{4}-\d{2}-\d{2})"', html)
    upload_date = upload_date_match.group(1) if upload_date_match else "Unknown"
    
    # --- BESKRIVNING ---
    # Söker efter "shortDescription":"..." i JSON-datan
    description_match = re.search(r'"shortDescription":"(.*?)"(?:,"isCrawlable")', html)
    if description_match:
        # Hantera escape-sekvenser som \n (ny rad) i beskrivningen
        description = description_match.group(1).encode().decode('unicode_escape')
    else:
        description = "No description available"
    
    # Returnera all metadata som en dictionary (inkl. html för återanvändning)
    return {
        "title_original": title_original,
        "title_clean": title_clean,
        "channel_original": channel_original,
        "channel_clean": channel_clean,
        "upload_date": upload_date,
        "description": description,
        "page_html": html,
    }


# =============================================================================
# FUNKTION: fetch_transcript
# =============================================================================
# Hämtar transkriptet (undertexterna) från YouTube-videon.
#
# YouTubeTranscriptApi är ett Python-bibliotek som:
# 1. Ansluter till YouTubes undertextserver
# 2. Hämtar tillgängliga undertexter (auto-genererade eller manuella)
# 3. Returnerar en lista med segment (text + tidsstämplar)
#
# Vi slår ihop alla segment till en enda textsträng.
# =============================================================================
def fetch_transcript(page_html: str, vid: str = None) -> str:
    """Fetch transcript using youtube_transcript_api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise ValueError("Missing package: youtube_transcript_api. Install with: pip install youtube_transcript_api")

    if not vid:
        vid_match = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', page_html)
        if not vid_match:
            raise ValueError("Could not determine video ID for transcript fetch")
        vid = vid_match.group(1)

    api = YouTubeTranscriptApi()
    transcript = api.fetch(vid)
    return " ".join(seg.text.replace("\n", " ") for seg in transcript if seg.text.strip())


# =============================================================================
# FUNKTION: estimate_tokens_and_cost
# =============================================================================
# Uppskattar antal tokens och kostnad för Claude API-anrop.
#
# Tokenuppskattning: Claude använder ungefär 1 token per 4 tecken för engelska,
# men det varierar beroende på språk och innehåll. Vi använder en konservativ
# uppskattning på 1 token per 3.5 tecken.
#
# Priser för Claude 3.5 Sonnet (januari 2026):
# - Input: $3 per miljon tokens
# - Output: $15 per miljon tokens
# =============================================================================
def estimate_tokens_and_cost(text: str) -> dict:
    """Estimate token count and API cost for summarization.
    
    Returns dict with:
        - input_tokens: Estimated input tokens
        - output_tokens: Estimated output tokens (for summary)
        - input_cost: Cost in USD for input
        - output_cost: Cost in USD for output
        - total_cost: Total estimated cost in USD
        - total_cost_sek: Total cost in SEK (approximate)
    """
    # Uppskatta input-tokens (transkript + prompt)
    # Använd 1 token per 3.5 tecken som konservativ uppskattning
    prompt_overhead = 200  # Tokens för instruktioner i prompten
    input_tokens = int(len(text) / 3.5) + prompt_overhead
    
    # Uppskatta output-tokens (sammanfattningen blir vanligtvis 500-1500 tokens)
    output_tokens = min(2048, max(500, input_tokens // 10))  # ~10% av input, max 2048
    
    # Claude Sonnet priser (USD per miljon tokens)
    INPUT_PRICE_PER_MILLION = 3.0    # $3 per 1M input tokens
    OUTPUT_PRICE_PER_MILLION = 15.0  # $15 per 1M output tokens
    
    input_cost = (input_tokens / 1_000_000) * INPUT_PRICE_PER_MILLION
    output_cost = (output_tokens / 1_000_000) * OUTPUT_PRICE_PER_MILLION
    total_cost = input_cost + output_cost
    
    # Uppskattad SEK-kurs (kan variera)
    USD_TO_SEK = 10.5
    total_cost_sek = total_cost * USD_TO_SEK
    
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": total_cost,
        "total_cost_sek": total_cost_sek
    }


# =============================================================================
# FUNKTION: save_transcript
# =============================================================================
# Sparar transkriptet till en textfil med metadata som header.
#
# Filnamnet blir: YYYYMMDD_HHMM_Kanalnamn_-_Videotitel.txt
# Exempel: 20260114_1530_Veritasium_-_How_Electricity_Actually_Works.txt
#
# Filen innehåller:
# 1. En header med metadata (kanal, titel, datum, URL, beskrivning)
# 2. En separator (---)
# 3. Själva transkriptet
# =============================================================================
def save_transcript(vid: str, metadata: dict, transcript: str, output_folder: Path) -> Path:
    """Save transcript to a text file with metadata header."""
    # Skapa tidsstämpel för filnamnet (ÅÅÅÅMMDD_TTMM)
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    
    # Skapa header med all metadata
    header = f"""This file is a transcript from YouTube
Channel: {metadata['channel_original']}
Title: {metadata['title_original']}
Upload date: {metadata['upload_date']}
URL: https://www.youtube.com/watch?v={vid}

Description:
{metadata['description']}

---

"""
    
    # Skapa output-mappen om den inte finns (parents=True skapar alla undermappar)
    output_folder.mkdir(parents=True, exist_ok=True)
    
    # Bygg filnamnet
    filename = f"{now}_{metadata['channel_clean']}_-_{metadata['title_clean']}.txt"
    filepath = output_folder / filename  # Path-objekt kan använda / för att slå ihop sökvägar
    
    # Skriv till fil med UTF-8 encoding (för att hantera svenska tecken etc.)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + transcript)
    
    return filepath


# =============================================================================
# FUNKTION: summarize_transcript
# =============================================================================
# Sammanfattar transkriptet med hjälp av Claude AI (Anthropic).
#
# Denna funktion:
# 1. Laddar API-nyckeln från .env-filen
# 2. Läser transkriptfilen
# 3. Trunkerar om texten är för lång (Claude har tokengräns)
# 4. Skickar till Claude API för sammanfattning
# 5. Visar sammanfattningen i terminalen
# 6. Sparar sammanfattningen till en separat fil
#
# Returnerar sökvägen till sammanfattningsfilen, eller None vid fel.
#
# OBS: Kräver en giltig ANTHROPIC_API_KEY i .env-filen
# =============================================================================
def summarize_transcript(filepath: Path, vid: str, title: str, language: str = "sv") -> Optional[Path]:
    """Summarize transcript using Claude AI.
    
    Args:
        filepath: Path to the transcript file
        vid: YouTube video ID
        title: Video title
        language: Summary language - "sv" for Swedish, "en" for English
    """
    # anthropic: Officiellt Python-bibliotek för Claude AI API
    try:
        import anthropic
    except ImportError:
        print("❌ Missing package: anthropic")
        print("   Install with: pip install anthropic")
        return None
    
    # Ladda miljövariabler från .env-filen
    # .env-filen ska innehålla: ANTHROPIC_API_KEY=sk-ant-xxxxx
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("⚠️ Warning: ANTHROPIC_API_KEY not found.")
        print("   Create a .env file with: ANTHROPIC_API_KEY=your-key-here")
        return None
    
    # Läs transkriptfilen
    with open(filepath, "r", encoding="utf-8") as f:
        transcript_content = f.read()
    
    print(f"📏 Transcript length: {len(transcript_content):,} characters")
    
    # Claude har en tokengräns (~200k tokens, ungefär 4 tecken per token)
    # Vi begränsar till 150k tecken för att ha marginal för prompt och svar
    MAX_CHARS = 150000
    if len(transcript_content) > MAX_CHARS:
        print(f"⚠️ Transcript too long, truncating to {MAX_CHARS:,} characters")
        transcript_content = transcript_content[:MAX_CHARS] + "\n\n[...truncated...]"
    
    # Skapa Claude-klienten (använder automatiskt ANTHROPIC_API_KEY från miljövariabler)
    try:
        client = anthropic.Anthropic()
    except anthropic.AuthenticationError:
        print("❌ Error: Invalid API key. Check your .env file.")
        return None
    
    print("🤖 Sending to Claude for summarization...")
    print("   (this may take 10-30 seconds)\n")
    
    # Skicka förfrågan till Claude API
    # - model: Vilken Claude-modell som ska användas
    # - max_tokens: Max antal tokens i svaret
    # - messages: Konversationen (här bara ett användarmeddelande)
    
    # Välj prompt baserat på språk
    if language == "en":
        prompt = f"""Summarize the following YouTube transcript. Include:
- Main points and key insights
- Important arguments or claims
- Any conclusions

Transcript:
{transcript_content}"""
        summary_label = "📝 SUMMARY"
        summary_header = f"Summary of: {title}"
        source_label = "Source"
    else:  # Swedish (default)
        prompt = f"""Sammanfatta följande YouTube-transkript. Inkludera:
- Huvudpoänger och nyckelinsikter
- Viktiga argument eller påståenden
- Eventuella slutsatser

Transkript:
{transcript_content}"""
        summary_label = "📝 SAMMANFATTNING"
        summary_header = f"Sammanfattning av: {title}"
        source_label = "Källa"
    
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
    except anthropic.APIError as e:
        print(f"❌ API Error: {e}")
        return None
    
    # Extrahera sammanfattningen från svaret
    # message.content är en lista med innehållsblock, vi tar första textblocket
    summary = message.content[0].text
    
    # Visa sammanfattningen i terminalen
    print("=" * 50)
    print(summary_label)
    print("=" * 50)
    print(summary)
    
    # Spara sammanfattningen till en separat fil
    # with_name() byter filnamnet, stem är filnamnet utan extension
    summary_filepath = filepath.with_name(filepath.stem + "_summary.txt")
    with open(summary_filepath, "w", encoding="utf-8") as f:
        f.write(f"{summary_header}\n")
        f.write(f"{source_label}: https://www.youtube.com/watch?v={vid}\n\n")
        f.write(summary)
    
    return summary_filepath


# =============================================================================
# FUNKTION: main
# =============================================================================
# Huvudfunktionen som körs när skriptet startas.
#
# Använder argparse för att hantera kommandoradsargument:
# - url: YouTube-URL (kan också anges interaktivt)
# - --summarize/-s: Aktiverar sammanfattning med Claude
# - --output/-o: Anger output-mapp
#
# Flödet:
# 1. Parsa kommandoradsargument
# 2. Hämta URL (från argument eller input)
# 3. Extrahera video-ID
# 4. Hämta metadata
# 5. Hämta transkript
# 6. Spara till fil
# 7. Visa token-uppskattning och kostnad
# 8. Fråga om sammanfattning (om inte --summarize angavs)
# 9. (Valfritt) Sammanfatta med Claude
# =============================================================================
def main():
    # argparse skapar en argumentparser som hanterar --help automatiskt
    parser = argparse.ArgumentParser(
        description="Download YouTube transcripts and optionally summarize with Claude AI"
    )
    # nargs="?" betyder att argumentet är valfritt
    parser.add_argument("url", nargs="?", help="YouTube video URL")
    # action="store_true" betyder att flaggan sätts till True om den anges
    parser.add_argument("--summarize", "-s", action="store_true", help="Summarize with Claude AI")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output folder (default: ./transcripts)")
    args = parser.parse_args()
    
    # Hämta URL - antingen från argument eller interaktivt
    if args.url:
        url = args.url
    else:
        url = input("URL: ").strip()  # .strip() tar bort whitespace
    
    if not url:
        print("❌ No URL provided")
        sys.exit(1)  # Avsluta med felkod 1
    
    # Spara om --summarize flaggan angavs (för att hoppa över frågan)
    force_summarize = args.summarize
    
    # Sätt output-mapp till samma mapp som skriptet + /transcripts
    # Path(__file__) är sökvägen till detta skript
    script_dir = Path(__file__).parent
    output_folder = args.output or script_dir / "transcripts"
    
    # try/except för att fånga och hantera fel på ett snyggt sätt
    try:
        # --- STEG 1: Extrahera video-ID ---
        print(f"🔍 Extracting video ID...")
        vid = extract_video_id(url)
        print(f"   Video ID: {vid}")
        
        # --- STEG 2: Hämta metadata ---
        print(f"📡 Fetching video metadata...")
        metadata = fetch_video_metadata(vid)
        print(f"   Title: {metadata['title_original']}")
        print(f"   Channel: {metadata['channel_original']}")
        print(f"   Upload date: {metadata['upload_date']}")
        
        # --- STEG 3: Hämta transkript ---
        print(f"📜 Fetching transcript...")
        transcript = fetch_transcript(metadata["page_html"], vid)
        print(f"   Length: {len(transcript):,} characters")  # :, formaterar med tusentalsavgränsare
        
        # --- STEG 4: Spara transkript ---
        print(f"💾 Saving transcript...")
        filepath = save_transcript(vid, metadata, transcript, output_folder)
        print(f"✅ Transcript saved!")
        print(f"📁 Path: {filepath}")
        
        # --- STEG 5: Visa token-info och fråga om sammanfattning ---
        # Beräkna uppskattad kostnad
        cost_info = estimate_tokens_and_cost(transcript)
        
        print(f"\n{'='*50}")
        print(f"💰 UPPSKATTAD KOSTNAD FÖR SAMMANFATTNING")
        print(f"{'='*50}")
        print(f"   📊 Input-tokens:  ~{cost_info['input_tokens']:,}")
        print(f"   📊 Output-tokens: ~{cost_info['output_tokens']:,}")
        print(f"   💵 Input-kostnad:  ${cost_info['input_cost']:.4f}")
        print(f"   💵 Output-kostnad: ${cost_info['output_cost']:.4f}")
        print(f"   💰 Total kostnad:  ${cost_info['total_cost']:.4f} (~{cost_info['total_cost_sek']:.2f} SEK)")
        print(f"{'='*50}")
        
        # Bestäm om sammanfattning ska göras
        do_summarize = force_summarize
        summary_language = "sv"  # Default svenska
        
        if not force_summarize:
            answer = input("\nVill du sammanfatta med Claude AI? (y/n): ").strip().lower()
            do_summarize = answer in ["j", "ja", "y", "yes"]
        
        # Fråga om språk för sammanfattningen om användaren vill ha en
        if do_summarize:
            print("\nVälj språk för sammanfattningen / Choose summary language:")
            print("  1. Svenska (Swedish)")
            print("  2. English (Engelska)")
            lang_answer = input("Välj/Choose (1/2): ").strip()
            if lang_answer == "2" or lang_answer.lower() in ["en", "english", "engelska"]:
                summary_language = "en"
            else:
                summary_language = "sv"
        
        # --- STEG 6: Sammanfatta (om --summarize angavs eller användaren sa ja) ---
        if do_summarize:
            print(f"\n🤖 Starting summarization...")
            summary_path = summarize_transcript(filepath, vid, metadata['title_original'], summary_language)
            if summary_path:
                print(f"\n✅ Summary saved: {summary_path}")
        
    # Fånga specifika fel och visa användarvänliga meddelanden
    except ValueError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ Network error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


# =============================================================================
# SKRIPTETS STARTPUNKT
# =============================================================================
# if __name__ == "__main__" är ett Python-idiom som kontrollerar om filen
# körs direkt (inte importeras som modul).
#
# När du kör: python yt_transcript_downloader.py
# Då är __name__ == "__main__" och main() körs.
#
# Om du importerar filen: from yt_transcript_downloader import fetch_transcript
# Då är __name__ == "yt_transcript_downloader" och main() körs INTE.
# =============================================================================
if __name__ == "__main__":
    main()
