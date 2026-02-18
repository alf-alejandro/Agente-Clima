import json
import re
import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

POSITION_PROMPT = """\
You are a weather prediction market analyst with access to real-time data.

I currently hold a NO position on this Polymarket weather market:
- City: {city}
- Date: {date}
- Question: {question}
- Entry NO price: {entry_no:.2f}  ({entry_no_pct:.0f}% implied probability NO)
- Current NO price: {current_no:.2f}  ({current_no_pct:.0f}%)
- Unrealized P&L: {pnl_sign}${pnl_abs:.2f}

My position profits if NO resolves (temperature does NOT exceed the threshold).

Steps:
1. Search for the LATEST weather forecast for {city} on {date}
2. Identify the temperature threshold from the question
3. Assess the current probability that temperature will NOT exceed the threshold
4. Decide: EXIT now or HOLD until resolution?

EXIT if the forecast now clearly shows temperature will exceed the threshold
   (position likely to lose — cut losses early).
EXIT if NO price has risen to near 0.97+ and most profit is already captured
   (lock in gains, reduce tail risk).
HOLD if forecast still strongly supports NO resolution and position has value.

Respond with ONLY valid JSON — no markdown, no explanation:
{{
  "forecast_high": <expected high as number, null if unavailable>,
  "unit": "F" or "C",
  "threshold": <threshold from the question as number>,
  "true_prob_no": <your current estimate 0.00 to 1.00>,
  "recommendation": "EXIT" or "HOLD",
  "reasoning": "<one sentence, max 15 words>",
  "data_quality": "HIGH" or "MEDIUM" or "LOW"
}}
"""


class WeatherAgent:
    def __init__(self, api_key, model="gemini-3-flash-preview"):
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
                    "AI pos: %s → %s (true=%.2f current_no=%.2f)",
                    pos.get("question", "")[:45],
                    result.get("recommendation"),
                    result.get("true_prob_no", 0),
                    pos.get("current_no", 0),
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
