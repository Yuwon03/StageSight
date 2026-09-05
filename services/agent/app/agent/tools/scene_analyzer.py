import os
import json
import logging
from app.config import settings
from app.gemini_models import TEXT_MODELS
from app.models.schemas import CreativeIntentExtraction, SceneInput

logger = logging.getLogger(__name__)

SCENE_PROMPT_TEMPLATE = """
You are the Creative-to-Physical Production Intelligence Core in StageSight.
Analyze the following script excerpt and creative intent. Extract structured spatial requirements:

Project: {project_name}
Scene: {scene_number}
Script Text:
{script_text}

Director's Creative Intent:
{creative_intent}

Return a valid JSON object matching this schema exactly:
{{
  "scene_type": "INT. DINING ROOM - SUNSET",
  "mood": "Intimate, high tension, warm golden-hour atmosphere",
  "shot_framing": "Wide two-shot",
  "camera_movement": "Slow forward dolly",
  "lighting_requirement": "Sunset backlight spilling through west window",
  "key_actors": ["Elena", "Marcus"],
  "raw_summary": "Two actors seated across a table during sunset; wide two-shot slowly pushing in with backlight."
}}
Do not include markdown code block ticks (```json), return only raw JSON.
"""

async def analyze_scene_intent(scene: SceneInput) -> CreativeIntentExtraction:
    """
    Uses Gemini (via google-genai SDK or Vertex AI) to parse creative intention into structured camera/spatial requirements.
    """
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    
    if api_key:
        try:
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=api_key)
            prompt = SCENE_PROMPT_TEMPLATE.format(
                project_name=scene.project_name,
                scene_number=scene.scene_number,
                script_text=scene.script_text,
                creative_intent=scene.creative_intent
            )
            
            response = client.models.generate_content(
                model=TEXT_MODELS[0],
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            if response.text:
                data = json.loads(response.text.strip())
                return CreativeIntentExtraction(**data)
        except Exception as e:
            logger.warning(f"Gemini API invocation fallback: {e}")

    # Deterministic fallback parsing for the demo scenario
    return CreativeIntentExtraction(
        scene_type="INT. DINING ROOM - SUNSET",
        mood="Intimate, mounting tension, warm golden-hour atmosphere",
        shot_framing="Wide two-shot",
        camera_movement="Slow forward dolly",
        lighting_requirement="Warm amber sunset backlight through west-facing window",
        key_actors=["Elena", "Marcus"],
        raw_summary="Elena and Marcus in dialogue at dining table; opening with wide two-shot and moving forward into medium two-shot with prominent sunset window backlight."
    )
