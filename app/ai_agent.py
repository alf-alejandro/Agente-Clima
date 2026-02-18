import json
import re
import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

PROMPT = """\
You are a weather prediction market analyst with access to real-time data.

Analyze this Polymarket weather market:
- City: {city}
- Date: {date}
- Question: {question}
- NO price: {no_price:.2f}  →  market implies {no_pct:.0f}% probability NO resolves
- YES price: {yes_price:.2f}

Steps:
1. Search for today's weather forecast for {city} on {date}
2. Identify the temperature threshold from the question
3. Estimate the TRUE meteorological probability that NO resolves
   (i.e. temperature does NOT exceed the threshold)
4. Compare your estimate to the market's {no_pct:.0f}% implied probability

Respond with ONLY valid JSON — no markdown, no explanation:
{{
  "forecast_high": <expected high as number, null if unavailable>,
  "unit": "F" or "C",
  "threshold": <threshold from the question as number>,
  "true_prob_no": <your estimate 0.00 to 1.00>,
  "recommendation": "ENTER" or "REDUCE" or "SKIP",
  "reasoning": "<one sentence, max 15 words>",
  "data_quality": "HIGH" or "MEDIUM" or "LOW"
}}

Recommendation rules:
- ENTER:  true_prob_no > {no_price:.2f} + 0.03  (clear positive edge)
- REDUCE: true_prob_no > {no_price:.2f} but edge < 0.03  (marginal, size down)
- SKIP:   true_prob_no <= {no_price:.2f}  (no edge or adverse — avoid)
"""


class WeatherAgent:
    def __init__(self, api_key, model="gemini-2.0-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def evaluate(self, opp):
        """
        Evaluate a single opportunity.
        Returns dict or None on failure.
        """
        try:
            prompt = self._build_prompt(opp)
            raw = self._call_gemini(prompt)
            result = self._parse_json(raw)
            if result:
                log.info(
                    "AI: %s → %s (true=%.2f mkt=%.2f)",
                    opp.get("question", "")[:45],
                    result.get("recommendation"),
                    result.get("true_prob_no", 0),
                    opp["no_price"],
                )
            return result
        except Exception:
            log.exception("AI eval failed: %s", opp.get("question", "")[:45])
            return None

    def _build_prompt(self, opp):
        city = opp.get("city", "unknown").replace("-", " ").title()
        date = datetime.now(timezone.utc).strftime("%B %d, %Y")
        return PROMPT.format(
            city=city,
            date=date,
            question=opp.get("question", ""),
            no_price=opp["no_price"],
            no_pct=opp["no_price"] * 100,
            yes_price=opp["yes_price"],
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
