"""Hindi in Devanagari script, turned into the plain Roman words the rest of the system already reads (stage 3b).

Nothing here answers anything. It rewrites a message so that "पोहा में कितनी कैलोरी है" reaches the pipeline as
"how much calories in poha", and "dal mein kitna protein hai" (Roman Hindi) as "how much protein in dal".

1. Every Devanagari word is looked up in a table of everyday words (questions, nutrients, conditions, foods). A word that is
   not in the table is spelled out letter by letter (poha, upma, kaddu), so a food name still reaches the food and dish lookup.
2. A few sentence shapes ("... mein kitni calories hai", "kya main ... kha sakta hoon") are rewritten as English questions.
3. Nothing is guessed: a word that is neither in the table nor a food the lookup knows stays an unknown word, and the lookup
   says so, as it does for any word it does not know.

The safety check sees the original Devanagari text as well as the rewritten text (see `pipeline.Engine.answer`), because the
emergency phrases include Devanagari ones.
"""
from __future__ import annotations

import re
import unicodedata

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_DEVANAGARI_WORD = re.compile(r"[ऀ-ॿ]+")
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUKTA, _HALANT, _CHANDRABINDU, _ANUSVARA, _VISARGA = "़", "्", "ँ", "ं", "ः"
_NUKTA_PAIRS = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f"}
_NUKTA_PRECOMPOSED = {"क़": "क़", "ख़": "ख़", "ग़": "ग़", "ज़": "ज़",
                      "ड़": "ड़", "ढ़": "ढ़", "फ़": "फ़"}

CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
    "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d",
    "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r",
    "ल": "l", "ळ": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h"}
VOWELS = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ओ": "o", "औ": "au", "ऑ": "o", "ऄ": "a"}
MATRAS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai",
          "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e", "ॆ": "e", "ॊ": "o"}


def has_devanagari(text: str) -> bool:
    return bool(DEVANAGARI.search(text or ""))


def norm_dev(word: str) -> str:
    """One spelling per word: nukta dots, chandrabindu and the zero-width marks do not change the lookup key."""
    word = unicodedata.normalize("NFC", word)
    for old, new in _NUKTA_PRECOMPOSED.items():
        word = word.replace(old, new)
    word = unicodedata.normalize("NFC", word)
    word = word.replace(_NUKTA, "").replace(_CHANDRABINDU, _ANUSVARA)
    return "".join(ch for ch in word if unicodedata.category(ch) != "Cf")


def _spell_out(word: str) -> str:
    """Letter-by-letter Roman spelling of one Devanagari word: long vowels shortened, the silent final 'a' dropped."""
    word = unicodedata.normalize("NFC", word)
    for old, new in _NUKTA_PRECOMPOSED.items():
        word = word.replace(old, new)
    word = unicodedata.normalize("NFC", word).replace(_CHANDRABINDU, _ANUSVARA).replace("ज्ञ", "ग्य")     # ज्ञ is said "gy"
    units: list[list] = []            # [consonant text or "", vowel text or None (= inherent a), halant?]
    i = 0
    while i < len(word):
        ch = word[i]
        if ch in CONSONANTS:
            text = CONSONANTS[ch]
            if i + 1 < len(word) and word[i + 1] == _NUKTA:
                text = _NUKTA_PAIRS.get(ch, text)
                i += 1
            unit = [text, None, False]
            if i + 1 < len(word) and word[i + 1] in MATRAS:
                unit[1] = MATRAS[word[i + 1]]
                i += 1
            elif i + 1 < len(word) and word[i + 1] == _HALANT:
                unit[1], unit[2] = "", True
                i += 1
            units.append(unit)
        elif ch in VOWELS:
            units.append(["", VOWELS[ch], False])
        elif ch == _ANUSVARA:
            if units:
                units[-1].append("n")
        elif ch == _VISARGA:
            if units:
                units[-1].append("h")
        i += 1
    out = []
    # silent a: the last syllable's, then any middle syllable between two sounded ones, working right to left
    last = len(units) - 1
    sounded = [u[1] != "" for u in units]
    if len(units) > 1 and units[last][1] is None:
        sounded[last] = False
    for idx in range(last - 1, 0, -1):
        if units[idx][1] is None and sounded[idx - 1] and sounded[idx + 1] and not (len(units[idx]) > 3):
            sounded[idx] = False
    for idx, unit in enumerate(units):
        vowel = unit[1]
        if vowel is None:
            vowel = "a" if sounded[idx] or len(units) == 1 else ""
        out.append(unit[0] + vowel + "".join(unit[3:]))
    return "".join(out)


# ---------------------------------------------------------------------------------------------------- the word table
_WORDS_TEXT = """
में mein | मे mein | मैं main | मै main | मुझे mujhe | मुझको mujhe | मेरा mera | मेरी meri | मेरे mere | को ko | से se | का ka | की ki | के ke
और aur | या ya | कौन kaun | सा sa | सी si | कौनसा kaunsa | कौनसी kaunsa | कौनसे kaunsa | कैसे kaise | क्यों kyon | क्या kya | है hai | हैं hain | था tha | थी thi
होता hota | होती hoti | होते hote | हो ho | हूँ hoon | हूं hoon | हुँ hoon | खा kha | खाऊं khaun | खाऊँ khaun | खाते khate | खाती khati | खाता khata
सकता sakta | सकती sakti | सकते sakte | चाहिए chahiye | चाहिये chahiye | चाहता chahta | चाहती chahti | बताओ batao | बताइए batao | बताइये batao | बताएं batao
दो do | दीजिए do | नहीं nahi | नही nahi | न na | हाँ haan | हां haan | जी ji | कृपया please | धन्यवाद thanks | शुक्रिया thanks | नमस्ते namaste | नमस्कार namaste
बनाऊं banau | बनाऊँ banau | बनाएं banaye | बनाये banaye | बनाए banaye | बनाना banaye | बनाते banaye | बनाती banaye | बनाता banaye | बनाकर banaye
रेसिपी recipe | पकौड़ा pakora | पकौड़े pakora | मशरूम mushroom | ओट्स oats | चुकंदर beetroot | विधि recipe | अच्छा accha | बेहतर better | ज्यादा zyada | ज़्यादा zyada | कम kam | बहुत bahut | रोज roz | रोज़ roz | आज aaj | रात raat
कैलोरी calories | कैलोरीज calories | कैलरी calories | कैलरीज calories | कैलोरीस calories | कैलोरीज़ calories | ऊर्जा energy | एनर्जी energy
प्रोटीन protein | कार्ब carb | कार्ब्स carbs | कार्बोहाइड्रेट carbohydrate | कार्बोहाइड्रेट्स carbohydrates | फैट fat | फ़ैट fat | वसा fat | चर्बी fat
चिकनाई fat | फाइबर fibre | फ़ाइबर fibre | रेशा fibre | रेशे fibre | पोषण nutrition | पोषक nutrients | तत्व nutrients | न्यूट्रिशन nutrition
शुगर sugar | चीनी cheeni | शक्कर shakkar | नमक namak | मिठाई mithai | मिठाइयां mithai | मिठाइयाँ mithai
मधुमेह diabetes | डायबिटीज diabetes | डायबिटीस diabetes | डायबिटिक diabetic | शुगर की बीमारी diabetes | मोटापा obesity | मोटा obese | वजन weight | वज़न weight
थायराइड thyroid | थायरॉइड thyroid | बीपी bp | ब्लड blood | प्रेशर pressure | रक्तचाप blood-pressure | उच्च high | पीसीओएस pcos | पीसीओडी pcod
एलर्जी allergy | एलर्जिक allergic | एलर्जियां allergies | एलर्जियाँ allergies | गर्भवती pregnant | गर्भावस्था pregnancy | दवा medicine | दवाई medicine
दवाइयां medicines | दवाइयाँ medicines | दवाएं medicines | इंसुलिन insulin | बीमारी illness | शाकाहारी vegetarian | मांसाहारी non-veg | जैन jain
नाश्ता breakfast | ब्रेकफास्ट breakfast | लंच lunch | डिनर dinner | स्नैक snack | स्नैक्स snacks | भूख hungry | भूखा hungry | भूखी hungry | खाना khana
रोटी roti | रोटियां roti | रोटियाँ roti | चपाती chapati | फुल्का phulka | चावल chawal | भात rice | दाल dal | आलू aloo | भिंडी bhindi | फूलगोभी phool-gobhi
फूल phool | गोभी gobhi | पत्तागोभी patta-gobhi | बंदगोभी band-gobhi | लौकी lauki | घिया ghiya | पालक palak | मटर matar | शकरकंद shakarkandi | शकरकन्द shakarkandi
दही dahi | पनीर paneer | प्याज pyaz | पियाज pyaz | टमाटर tamatar | खीरा kheera | ककड़ी kakdi | बादाम badam | अखरोट akhrot | मूंगफली moongphali
मूँगफली moongphali | मूंग moong | छोले chole | चना chana | अंकुरित sprouts | स्प्राउट्स sprouts | अंडा anda | अंडे anda | अण्डा anda | इडली idli | आम aam
केला kela | घी ghee | तेल oil | जीरा jeera | हल्दी haldi | धनिया dhania | मिर्च mirch | लाल lal | हरी hari | लहसुन lahsun | अदरक adrak
पोहा poha | उपमा upma | खिचड़ी khichdi | दलिया daliya | समोसा samosa | पूरी poori | पराठा paratha | डोसा dosa | बिरयानी biryani | राजमा rajma
सांभर sambar | चाय chai | कॉफी coffee | दूध milk | लस्सी lassi | छाछ chaas | सेब apple | संतरा orange | अनार pomegranate | पपीता papaya
अमरूद guava | अंगूर grapes | तरबूज watermelon | मछली fish | चिकन chicken | मटन mutton | सूप soup | सलाद salad | सब्जी sabji | सब्जियां sabji | सब्जियाँ sabji
फल fruit | फलों fruit | नारियल coconut | मखाना makhana | मक्खन butter | मलाई malai | चटनी chutney | अचार achar | पापड papad | रायता raita | हलवा halwa
"""
WORDS: dict[str, str] = {}
for _chunk in re.split(r"\s*\|\s*|\n", _WORDS_TEXT):
    _chunk = _chunk.strip()
    if _chunk:
        _dev, _roman = _chunk.rsplit(" ", 1)
        WORDS[norm_dev(_dev)] = _roman.replace("-", " ")


def roman_word(word: str) -> str:
    return WORDS.get(norm_dev(word)) or _spell_out(word)


def _romanise(text: str) -> str:
    text = text.translate(_DIGITS).replace("।", ". ").replace("॥", ". ")
    return _DEVANAGARI_WORD.sub(lambda m: " " + roman_word(m.group(0)) + " ", text)


# ------------------------------------------------------------------------------------------------ sentence shapes
_NUT = r"(?:calories|calorie|kcal|carbs?|carbohydrates?|protein|fat|fibre|fiber|energy|nutrition|nutrients)"
_HOW = r"(?:kitna|kitni|kitne)"
_BE = r"(?:\s+(?:hota|hoti|hote))?(?:\s+(?:hai|hain|ho))?"
_SHAPES = [
    # "poha mein kitni calories hai" / "dal mein protein kitna hota hai"
    (re.compile(rf"^(?P<x>.+?)\s+(?:mein|me|main)\s+{_HOW}\s+(?P<n>{_NUT}){_BE}\s*\??$"), r"how much \g<n> in \g<x>"),
    (re.compile(rf"^(?P<x>.+?)\s+(?:mein|me|main)\s+(?P<n>{_NUT})\s+{_HOW}{_BE}\s*\??$"), r"how much \g<n> in \g<x>"),
    # "dal ki calories kitni hai"
    (re.compile(rf"^(?P<x>.+?)\s+(?:ki|ka|ke)\s+(?P<n>{_NUT})\s+{_HOW}{_BE}\s*\??$"), r"how much \g<n> in \g<x>"),
    (re.compile(rf"^(?P<x>.+?)\s+(?:ki|ka|ke)\s+{_HOW}\s+(?P<n>{_NUT}){_BE}\s*\??$"), r"how much \g<n> in \g<x>"),
    # "kya main chawal kha sakta hoon"
    (re.compile(r"^(?:kya\s+)?(?:main|mai)\s+(?P<x>.+?)\s+(?:kha|khaun|le|pi|peeta|pee)\s+(?:sakta|sakti|sakte)(?:\s+(?:hoon|hun|hu|hain))?\s*\??$"),
     r"can i eat \g<x>"),
    (re.compile(r"^(?:kya\s+)?(?P<x>.+?)\s+(?:kha|khana)\s+(?:sakta|sakti|sakte)\s+(?:hoon|hun|hu|hain)\s*\??$"), r"can i eat \g<x>"),
    # recipes
    (re.compile(r"^(?P<x>.+?)\s+(?:ki|ka)\s+recipe(?:\s+(?:batao|do|chahiye))?\s*\??$"), r"recipe for \g<x>"),
    (re.compile(r"^(?P<x>.+?)\s+kaise\s+(?:banaye|banate|banau|banta|banti|banaen|banayen)(?:\s+(?:hain|hai|hoon))?\s*\??$"), r"how to make \g<x>"),
    (re.compile(r"^(?:mujhe\s+)?(?P<x>.+?)\s+(?:ki\s+)?recipe\s+chahiye\s*\??$"), r"recipe for \g<x>"),
    # "x aur y mein se kaun sa behtar hai"
    (re.compile(r"^(?P<x>.+?)\s+aur\s+(?P<y>.+?)\s+(?:mein|me)\s+se\s+(?:kaun\s+sa|kaun\s+si|kon\s+sa|kaun|kaunsa|kon|konsa)\s+(?:accha|achha|better|behtar)(?:\s+hai)?\s*\??$"),
     r"\g<x> vs \g<y>"),
    # "mujhe peanuts se allergy hai"
    (re.compile(r"^(?:mujhe\s+)?(?P<x>.+?)\s+se\s+(?:allergy|allergic)(?:\s+(?:hai|hain|hoon|hun))?\s*\??$"), r"i am allergic to \g<x>"),
    (re.compile(r"^(?:mujhe\s+)?(?P<x>diabetes|thyroid|bp|pcos|pcod|obesity|blood pressure|high blood pressure)\s+(?:hai|hain)\s*\??$"), r"i have \g<x>"),
    # "poha kya hai" (a bare dish name: handled by the dish lookup)
    (re.compile(r"^(?P<x>.+?)\s+kya\s+(?:hai|hota\s+hai)\s*\??$"), r"what is \g<x>"),
    (re.compile(r"^(?:mujhe\s+)?(?P<x>.+?)\s+(?:khana|khani)\s+(?:hai|chahiye)\s*\??$"), r"i want \g<x>"),
]


def translate(text: str) -> str:
    """The message in the Roman words the pipeline reads. Text with no Devanagari and no Hindi sentence shape comes back unchanged."""
    if not text:
        return text
    original = text
    rewritten = _romanise(text) if has_devanagari(text) else text
    simple = " ".join(re.sub(r"[^\w\s?']", " ", rewritten.lower().replace("-", " ")).split())
    simple = simple.replace(" ?", "?")
    for pattern, template in _SHAPES:
        m = pattern.match(simple)
        if m:
            simple = pattern.sub(template, simple)
            return " ".join(simple.split())
    if rewritten == original:
        return original
    return " ".join(rewritten.split())
