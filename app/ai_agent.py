import json
import re
import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

POSITION_PROMPT = """\
You are an expert prediction market analyst specializing in weather derivatives.
You have access to real-time web search. Use it aggressively across MULTIPLE sources.

I currently hold a NO position on this Polymarket weather market:
- City: {city}
- Date: {date}
- Question: {question}
- Entry NO price: {entry_no:.2f}  ({entry_no_pct:.0f}% implied probability NO)
- Current NO price: {current_no:.2f}  ({current_no_pct:.0f}%)
- Unrealized P&L: {pnl_sign}${pnl_abs:.2f}

My position profits if NO resolves (temperature does NOT exceed the threshold).

Research steps — search ALL of the following before deciding:
1. WEATHER FORECASTS: Search at least 3 different sources for {city} on {date}:
   - National Weather Service / official meteorological agency for {city}
   - Weather.com, AccuWeather, Weather Underground, Wunderground, Meteoblue
   - Local TV station forecasts or any regional forecast model (GFS, ECMWF, NAM)
   Cross-check forecasts and note any disagreement between models.

2. PREDICTION MARKETS: Search for the same or similar market on:
   - Polymarket: search "polymarket {city} temperature {date}"
   - Kalshi: search "kalshi {city} weather {date}"
   - Note the current NO/YES prices and any price movement trends.
   Market price movement is a strong signal — if YES is rising sharply, the crowd
   knows something.

3. SYNTHESIS: Identify the temperature threshold from the question.
   Weight sources by recency and reliability. If forecasts disagree, lean toward
   the most recent official model run.

Decision rules:
EXIT if multiple forecasts show temperature will exceed the threshold
   (position likely to lose — cut losses before it worsens).
EXIT if NO price has risen to 0.97+ and most profit is already locked
   (harvest gains, eliminate remaining tail risk).
EXIT if prediction market prices (Kalshi/Polymarket) show strong YES momentum
   that contradicts our thesis.
HOLD if the preponderance of forecasts and market signals still support NO.

Respond with ONLY valid JSON — no markdown, no explanation outside the JSON:
{{
  "forecast_high": <consensus expected high as number, null if unavailable>,
  "unit": "F" or "C",
  "threshold": <threshold from the question as number>,
  "sources_checked": ["list", "of", "source", "names", "actually", "searched"],
  "market_signal": "<brief note on Polymarket/Kalshi price or 'not found'>",
  "true_prob_no": <your synthesized estimate 0.00 to 1.00>,
  "recommendation": "EXIT" or "HOLD",
  "reasoning": "<one sentence, max 20 words>",
  "data_quality": "HIGH" or "MEDIUM" or "LOW"
}}
"""


class WeatherAgent:
    def __init__(self, api_key, model="gemini-3-pro-preview"):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    # ── Take-profit evaluation (primary use) ───────────────────────────────────

    def evaluate_position(self, pos):
        """Evaluate an open position for EXIT/HOLD take-profit decision.
        Returns dict with 'recommendation': 'EXIT' or 'HOLD', or None on failure.
        """
        try:
            prompt = self._build_position_prompt(pos)
            raw = self._call_gemini(prompt)
            result = self._parse_json(raw)
            if result:
                log.info(
                    "AI pos: %s → %s (true=%.2f mkt=%s quality=%s)",
                    pos.get("question", "")[:45],
                    result.get("recommendation"),
                    result.get("true_prob_no", 0),
                    result.get("market_signal", "-")[:30],
                    result.get("data_quality", "?"),
                )
            return result
        except Exception:
            log.exception("AI position eval failed: %s", pos.get("question", "")[:45])
            return None

    def _build_position_prompt(self, pos):
        city = pos.get("city", "unknown").replace("-", " ").title()
        date = datetime.now(timezone.utc).strftime("%B %d, %Y")
        entry_no = pos.get("entry_no", 0)
        current_no = pos.get("current_no", entry_no)
        allocated = pos.get("allocated", 0)
        tokens = pos.get("tokens", 0)
        pnl = tokens * current_no - allocated
        return POSITION_PROMPT.format(
            city=city,
            date=date,
            question=pos.get("question", ""),
            entry_no=entry_no,
            entry_no_pct=entry_no * 100,
            current_no=current_no,
            current_no_pct=current_no * 100,
            pnl_sign="+" if pnl >= 0 else "-",
            pnl_abs=abs(pnl),
        )

    def _call_gemini(self, prompt):
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ]
        tools = [types.Tool(googleSearch=types.GoogleSearch())]
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=-1),
            tools=tools,
        )
        text = ""
        for chunk in self.client.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                text += chunk.text
        return text

    def _parse_json(self, text):
        if not text:
            return None
        # Strip markdown code blocks
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        # Raw JSON object
        m = re.search(r"\{[^{}]+\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
        return None
