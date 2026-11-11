"""
Natural-language prompt -> validated editing recipe.

Two interpreters produce the same output (a partial recipe that is merged
over the current one and then validated by recipe.validate):

* RuleInterpreter (default, offline): a transparent phrase table.  Every
  match is reported back to the user so they can see why a slider moved.
* OpenRouterInterpreter (optional): sends ONLY the prompt text, the current
  recipe and the schema to an OpenRouter-compatible chat endpoint.  The
  photograph is never transmitted.  The reply must be JSON; it is parsed as
  data and validated.  Nothing it returns is executed.
"""

from __future__ import annotations

import copy
import json
import os
import re
import urllib.error
import urllib.request

from . import recipe as R

# --------------------------------------------------------------------------------
# Rule tables.  Deltas are ADDED to the current value (then clamped).
# --------------------------------------------------------------------------------

STOCK_RULES = [  # (regex, film stock, explanation) - first match in the prompt wins
    (r"portra\s*160", "Studio / Portra 160-inspired", "Portra 160-inspired film"),
    (r"portra\s*800", "Studio / Portra 800-inspired", "Portra 800-inspired film"),
    (r"portra", "Studio / Portra 400-inspired", "Portra 400-inspired film"),
    (r"kodak\s*gold|gold\s*200|\bgold\b", "Studio / Gold 200-inspired", "Kodak Gold-inspired film"),
    (r"ektar", "Studio / Ektar 100-inspired", "Ektar-inspired film"),
    (r"400h|pro\s*400", "Studio / Fuji Pro 400H-inspired", "Fuji Pro 400H-inspired film"),
    (r"velvia", "Studio / Fuji Velvia-inspired", "Velvia-inspired slide film"),
    (r"superia|fuji(film)?", "Studio / Fuji Superia-inspired", "Fujifilm-inspired film"),
    (r"cinestill\s*50|50d", "Studio / Cinestill 50D-inspired", "Cinestill 50D-inspired film"),
    (r"cinestill|800t", "Studio / Cinestill 800T-inspired", "Cinestill 800T-inspired film"),
    (r"500t|vision\s*3?\s*500|tungsten", "Studio / Vision3 500T-inspired", "Vision3 500T-inspired cinema film"),
    (r"vision\s*3|250d|motion picture|cinema film", "Studio / Vision3 250D-inspired",
     "Vision3-inspired cinema film"),
    (r"tri-?x", "Studio / Kodak Tri-X-inspired (B&W)", "Tri-X-inspired B&W"),
    (r"delta\s*100", "Studio / Ilford Delta 100-inspired (B&W)", "Delta 100-inspired B&W"),
    (r"fp4", "Studio / Ilford FP4 Plus-inspired (B&W)", "FP4-inspired B&W"),
    (r"ilford|hp5|black\s*(and|&)\s*white|b\s*&\s*w|\bbw\b|monochrome|greyscale|grayscale",
     "Studio / Ilford HP5 Plus-inspired (B&W)", "Ilford HP5-inspired B&W"),
    (r"bleach\s*bypass", "Studio / Bleach bypass", "bleach-bypass look"),
    (r"expired", "Studio / Expired colour film", "expired-film look"),
]

# Style rules: (regex, default stock or None, deltas, explanation)
STYLE_RULES = [
    (r"\b(19)?70'?s\b|seventies", "Studio / 1970s warm print",
     {"basic.temperature": 12, "basic.contrast": -10, "basic.highlights": -20, "basic.saturation": -8,
      "effects.fade": 14, "effects.grain_amount": 25, "effects.grain_size": 40, "effects.vignette_amount": -15,
      "hsl.green_saturation": -20, "hsl.green_hue": 15}, "1970s: warm faded print, muted greens, grain"),
    (r"\b(19)?80'?s\b|eighties", "Studio / Fuji Superia-inspired",
     {"basic.contrast": 8, "basic.saturation": 10, "effects.grain_amount": 18, "basic.tint": 5,
      "color_grading.shadows_hue": 260, "color_grading.shadows_saturation": 12},
     "1980s: punchy consumer colour, magenta-blue shadows"),
    (r"\b(19)?90'?s\b|nineties", "Studio / Gold 200-inspired",
     {"basic.contrast": 6, "effects.grain_amount": 15, "effects.fade": 5, "basic.temperature": 6},
     "1990s: warm consumer negative"),
    (r"\b(20)?00'?s\b|y2k|two thousands", "Studio / Gold 200-inspired",
     {"basic.temperature": 14, "basic.vibrance": 12, "effects.halation_amount": 20, "effects.halation_radius": 40,
      "effects.fade": 6, "color_grading.highlights_hue": 40, "color_grading.highlights_saturation": 20},
     "2000s: warm glowing highlights, bloom"),
    (r"music[\s-]*video", None,
     {"effects.halation_amount": 15, "basic.vibrance": 10, "color_grading.highlights_saturation": 10,
      "color_grading.highlights_hue": 35}, "music-video glow and colour"),
    (r"independent film|indie film|arthouse", "Studio / Vision3 250D-inspired",
     {"basic.contrast": -6, "effects.fade": 8, "basic.saturation": -8, "effects.grain_amount": 15},
     "independent film: soft, restrained, filmic"),
    (r"cinematic|film still|movie|hollywood", "Studio / Cinematic teal & orange",
     {"basic.contrast": 10, "basic.highlights": -20, "effects.vignette_amount": -15},
     "cinematic: teal/orange split, controlled highlights"),
    (r"documentary|reportage|photojournalis", "Studio / Documentary natural",
     {"basic.contrast": 5, "basic.vibrance": 4}, "documentary: neutral and honest"),
    (r"natural|realistic|true[\s-]to[\s-]life", None,
     {"basic.saturation": -3}, "natural rendering"),
    (r"vintage|retro|old photo|nostalgi", "Studio / Vintage faded",
     {"effects.fade": 10, "basic.temperature": 8, "effects.grain_amount": 15}, "vintage/nostalgic"),
    (r"analog(ue)?|film (look|photo|photograph)|shot on film", None,
     {"effects.grain_amount": 20, "effects.grain_size": 30, "basic.highlights": -15},
     "analog film: grain and highlight roll-off"),
    (r"moody|brooding|melanchol", None,
     {"basic.exposure": -0.3, "basic.contrast": 10, "basic.highlights": -25, "basic.saturation": -15,
      "effects.vignette_amount": -20}, "moody: darker, desaturated, vignette"),
    (r"atmospheric|ethereal", None,
     {"effects.dehaze": -10, "basic.highlights": -15, "effects.fade": 6}, "atmospheric: soft haze"),
    (r"earthy|earth tones?", None,
     {"basic.temperature": 10, "hsl.green_hue": 20, "hsl.green_saturation": -30, "hsl.blue_saturation": -25,
      "hsl.orange_saturation": 8, "hsl.yellow_saturation": -10}, "earthy: warm, ochre greens, quiet blues"),
    (r"dreamy|soft focus|romantic", None,
     {"effects.clarity": -20, "basic.highlights": -15, "effects.halation_amount": 12, "effects.fade": 6},
     "dreamy: softer local contrast and glow"),
    (r"film noir|noir", "Studio / Kodak Tri-X-inspired (B&W)",
     {"basic.contrast": 25, "effects.vignette_amount": -30, "basic.blacks": -15}, "noir: hard B&W"),
    (r"teal (and|&) orange|orange (and|&) teal", "Studio / Cinematic teal & orange", {},
     "teal & orange grade"),
    (r"pastel", None, {"basic.saturation": -15, "effects.fade": 8, "basic.shadows": 15},
     "pastel: light, low saturation"),
    (r"summer|sunny|golden hour", None,
     {"basic.temperature": 15, "color_grading.highlights_hue": 42, "color_grading.highlights_saturation": 18},
     "sunny warmth"),
    (r"winter|cold|icy|frosty", None, {"basic.temperature": -18, "basic.saturation": -10}, "cold"),
    (r"night|nocturnal", None, {"basic.temperature": -10, "color_grading.shadows_hue": 215,
                                "color_grading.shadows_saturation": 20}, "night tones"),
]

MODIFIER_RULES = [  # (regex, deltas, explanation)
    (r"warm(er|th)?|golden", {"basic.temperature": 15}, "warmer"),
    (r"cool(er)?|colder|blu(e|ish) (cast|tone)", {"basic.temperature": -15}, "cooler"),
    (r"dark(er|en)?|low[\s-]?key|underexpos", {"basic.exposure": -0.45, "basic.shadows": -8}, "darker"),
    (r"bright(er|en)?|light(er|en)|airy|high[\s-]?key", {"basic.exposure": 0.4, "basic.shadows": 10}, "brighter"),
    (r"(more|high|strong|punchy|hard)[\s-]+contrast|contrasty|punchy", {"basic.contrast": 20}, "more contrast"),
    (r"(less|low|soft|flat)[\s-]+contrast|flat", {"basic.contrast": -20}, "less contrast"),
    (r"desaturat|muted|washed[\s-]out|subdued", {"basic.saturation": -25}, "desaturated"),
    (r"vibrant|vivid|saturated|colou?rful|rich colou?r", {"basic.vibrance": 20, "basic.saturation": 8},
     "more saturated"),
    (r"soft(er)? highlights?|highlight roll[\s-]?off|tame highlights", {"basic.highlights": -30, "basic.whites": -10},
     "soft highlights"),
    (r"(crushed|deep|rich|inky) (blacks|shadows)", {"basic.blacks": -20, "basic.shadows": -10}, "deep blacks"),
    (r"(lifted|faded|milky|matte) (blacks|shadows)|matte", {"effects.fade": 15}, "lifted/faded blacks"),
    (r"open (up )?(the )?shadows|shadow detail", {"basic.shadows": 25}, "open shadows"),
    (r"grain(y)?", {"effects.grain_amount": 25}, "film grain"),
    (r"heavy grain|lots of grain|gritty", {"effects.grain_amount": 25, "effects.grain_size": 25}, "heavy grain"),
    (r"vignett", {"effects.vignette_amount": -25}, "vignette"),
    (r"glow|bloom|halation", {"effects.halation_amount": 25}, "highlight glow"),
    (r"haz(e|y)|misty|foggy", {"effects.dehaze": -15}, "haze"),
    (r"dehaze|clear(er)? sky|remove haze", {"effects.dehaze": 25}, "dehaze"),
    (r"crisp|sharp(er|en)?|detailed", {"effects.clarity": 12, "effects.texture": 10}, "crisper"),
    (r"soft(er)?\b(?! highlight)|gentle", {"effects.clarity": -10}, "softer"),
    (r"green(ish)? tint", {"basic.tint": -12}, "green tint"),
    (r"magenta|pink(ish)? tint", {"basic.tint": 12}, "magenta tint"),
]

COLOR_WORDS = {"red": ["red"], "orange": ["orange", "skin", "skin tone"], "yellow": ["yellow"],
               "green": ["green", "foliage", "grass", "leaves", "trees"], "aqua": ["aqua", "teal", "cyan"],
               "blue": ["blue", "sky", "skies", "water"], "purple": ["purple", "violet"],
               "magenta": ["magenta", "pink"]}
COLOR_ADJ = {  # adjective -> (saturation, luminance, hue)
    "muted": (-35, 0, 0), "desaturated": (-45, 0, 0), "subdued": (-30, 0, 0), "faded": (-30, 5, 0),
    "vivid": (30, 0, 0), "saturated": (30, 0, 0), "rich": (25, -8, 0), "vibrant": (30, 0, 0),
    "deep": (15, -20, 0), "dark": (0, -25, 0), "darker": (0, -25, 0), "bright": (10, 15, 0),
    "brighter": (10, 15, 0), "lighter": (0, 15, 0), "pastel": (-30, 15, 0), "olive": (-25, -5, 20),
    "warm": (5, 0, -15), "cool": (0, 0, 15), "golden": (10, 5, -10), "teal": (0, 0, 20),
}
ZONE_HUES = {"teal": 190, "cyan": 185, "blue": 225, "green": 130, "orange": 30, "golden": 45, "warm": 38,
             "cool": 210, "magenta": 310, "purple": 280, "red": 0, "yellow": 55, "amber": 40, "pink": 330}

INTENSITY = [(r"\b(very|extremely|super|really|heavily|strongly|much)\b", 1.6),
             (r"\b(slightly|subtle|subtly|a bit|a little|a touch|gently|lightly|hint of|somewhat)\b", 0.5)]

NEGATION = re.compile(r"\b(no|without|not|avoid|less)\s+(\w+\s+)?$")


def _intensity(prefix):
    w = prefix[-40:]
    for pat, f in INTENSITY:
        if re.search(pat + r"[\s\w-]{0,20}$", w):
            return f
    return 1.0


def _add(acc, path, delta):
    acc[path] = acc.get(path, 0.0) + delta


class RuleInterpreter:
    name = "Local rules (offline)"

    def interpret(self, text, current=None):
        """Return (recipe, explanation_lines, warnings)."""
        base = copy.deepcopy(current) if current is not None else R.default_recipe()
        t = " " + text.lower().replace("’", "'") + " "
        acc, notes, stock, stock_reason = {}, [], None, None

        for pat, st, why in STOCK_RULES:
            m = re.search(pat, t)
            if m:
                stock, stock_reason = st, f"“{m.group(0).strip()}” → {why}"
                break
        for pat, st, deltas, why in STYLE_RULES:
            for m in re.finditer(pat, t):
                pre = t[:m.start()]
                if NEGATION.search(pre):
                    continue
                k = _intensity(pre)
                for p, d in deltas.items():
                    _add(acc, p, d * k)
                if st and stock is None:
                    stock, stock_reason = st, f"“{m.group(0).strip()}” → {why} ({st.split(' / ')[1]})"
                else:
                    notes.append(f"“{m.group(0).strip()}” → {why}" + (" ×%.1f" % k if k != 1 else ""))
                break
        consumed = []
        # colour-specific phrases: "muted greens", "deep blue sky"
        cw = "|".join(sorted({w for ws in COLOR_WORDS.values() for w in ws}, key=len, reverse=True))
        for m in re.finditer(rf"\b({'|'.join(COLOR_ADJ)})\s+({cw})(e?s)?\b", t):
            adj, word = m.group(1), m.group(2)
            band = next(b for b, ws in COLOR_WORDS.items() if word in ws)
            k = _intensity(t[:m.start()])
            s, l_, h = COLOR_ADJ[adj]
            if s:
                _add(acc, f"hsl.{band}_saturation", s * k)
            if l_:
                _add(acc, f"hsl.{band}_luminance", l_ * k)
            if h:
                _add(acc, f"hsl.{band}_hue", h * k)
            consumed.append((m.start(), m.end()))
            notes.append(f"“{m.group(0).strip()}” → {band} band: sat {s * k:+.0f}, lum {l_ * k:+.0f}, hue {h * k:+.0f}")
        # zone tints: "teal shadows", "golden highlights"
        for m in re.finditer(rf"\b({'|'.join(ZONE_HUES)})(ish)?\s+(shadows|highlights|midtones|mids)\b", t):
            zone = {"mids": "midtones"}.get(m.group(3), m.group(3))
            k = _intensity(t[:m.start()])
            R.set_(base, f"color_grading.{zone}_hue", ZONE_HUES[m.group(1)])
            _add(acc, f"color_grading.{zone}_saturation", 25 * k)
            consumed.append((m.start(), m.end()))
            notes.append(f"“{m.group(0).strip()}” → {zone} tinted (hue {ZONE_HUES[m.group(1)]}°)")

        def _is_consumed(s, e):
            return any(a <= s and e <= b for a, b in consumed)

        for pat, deltas, why in MODIFIER_RULES:
            for m in re.finditer(rf"\b(?:{pat})", t):
                if _is_consumed(m.start(), m.end()):
                    continue
                pre = t[:m.start()]
                if NEGATION.search(pre):
                    for p in deltas:
                        if p.startswith("effects.grain") or p.startswith("effects.vignette") \
                                or p.startswith("effects.halation"):
                            acc[p] = -1e9   # explicit "no grain" etc.: force to minimum
                    notes.append(f"“{pre.split()[-1]} {m.group(0).strip()}” → {why} suppressed")
                    break
                k = _intensity(pre)
                for p, d in deltas.items():
                    _add(acc, p, d * k)
                notes.append(f"“{m.group(0).strip()}” → {why}" + (" ×%.1f" % k if k != 1 else ""))
                break

        if re.search(r"\b(subtle|subtly|gentle|slight(ly)?)\b", t) and not acc:
            pass
        partial = {}
        for path, d in acc.items():
            g, key = path.split(".")
            cur = R.get(base, path)
            partial.setdefault(g, {})[key] = R.NUMERIC_BY_PATH[path].lo if d < -1e8 else cur + d
        if stock:
            partial.setdefault("film", {})["stock"] = stock
            if R.get(base, "film.strength") == 0:
                partial["film"]["strength"] = 85
            notes.insert(0, stock_reason)
        if not partial:
            return base, ["No recognised phrases. Try words like warm, moody, faded, Portra, 1970s, muted greens."], []
        rec, warnings = R.validate(partial, partial=True, base=base)
        rec["notes"] = f"Prompt: {text.strip()}"[:500]
        return rec, notes, [w for w in warnings if "clamped" not in w]


# --------------------------------------------------------------------------------
# Optional OpenRouter-compatible LLM (text only)
# --------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the colour-grading assistant of a photo editor. You translate a user's request into
edit parameters. You never see the photograph and must not ask for it. Respond with ONE JSON object and
nothing else. Include only the parameters you want to change, with ABSOLUTE target values (not deltas),
inside their sections, respecting the JSON schema limits. Use "notes" for a one-sentence explanation.
Keep the photograph's content untouched: you only control global colour and tone. Prefer subtle, tasteful
values; extreme values are rarely right. Never add grain, sharpening, noise reduction or vignette unless
the request implies it."""


def get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    try:  # Colab secrets panel
        from google.colab import userdata  # type: ignore
        return userdata.get("OPENROUTER_API_KEY")
    except Exception:
        return None


def extract_json(text):
    """First top-level JSON object in `text` (tolerates ```json fences)."""
    text = re.sub(r"```(json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object")


class OpenRouterInterpreter:
    name = "OpenRouter LLM (text only)"

    def __init__(self, api_key=None, model=None, base_url=None, timeout=45):
        self.api_key = api_key or get_api_key()
        self.model = model or os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-5.5")
        self.base_url = (base_url or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip("/")
        self.timeout = timeout

    def available(self):
        return bool(self.api_key)

    def build_request(self, text, current):
        schema = R.json_schema()
        cur = copy.deepcopy(current)
        cur["match"].pop("data", None)
        user = (f"Current recipe:\n{json.dumps(cur, separators=(',', ':'))}\n\n"
                f"Available film stocks: {json.dumps(R.REGISTRIES['film.stock'][:60])}\n"
                f"Schema:\n{json.dumps(schema, separators=(',', ':'))}\n\nRequest: {text}")
        return {"model": self.model, "temperature": 0.2, "max_tokens": 1500,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]}

    def interpret(self, text, current=None):
        current = current if current is not None else R.default_recipe()
        if not self.api_key:
            raise RuntimeError("No OPENROUTER_API_KEY (Colab: add it in the Secrets panel 🔑).")
        body = json.dumps(self.build_request(text, current)).encode()
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json",
                                              "X-Title": "Darkroom Studio"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"LLM request failed: HTTP {e.code} {e.read()[:200]!r}") from e
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        data = extract_json(content)
        rec, warnings = R.validate(data, partial=True, base=current)
        notes = [f"LLM ({self.model}): {data.get('notes', '')}".strip()] + [f"→ {d}" for d in R.diff(rec)][:25]
        rec["notes"] = f"Prompt: {text.strip()}"[:500]
        return rec, notes, warnings
