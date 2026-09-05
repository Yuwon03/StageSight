from typing import List
from app.models.schemas import (
    ProductionAlternative, OpticsCalculation, FloorPlan, DepartmentConflict
)

def generate_production_alternatives(
    optics: OpticsCalculation,
    floor_plan: FloorPlan,
    conflicts: List[DepartmentConflict]
) -> List[ProductionAlternative]:
    """
    Generates actionable, creative, and physical alternatives to resolve department conflicts.
    """
    alternatives: List[ProductionAlternative] = []

    # Alternative 1: Lens Swap (Recommended if optics depth deficit)
    if optics.has_clearance_conflict:
        alternatives.append(ProductionAlternative(
            id="alt_lens_28mm",
            category="Optics & Lensing",
            title="Swap 35mm Lens to 28mm Focal Length",
            impact_description=(
                "Switching to a 28mm prime lens expands the horizontal FOV to 65.5°, reducing required subject distance "
                f"from {optics.minimum_camera_depth_m:.1f}m to 2.2m. This completely resolves the camera depth deficit."
            ),
            tradeoffs="Slightly more background environment visible in frame; requires actor positions to be well dressed.",
            recommended=True
        ))

    # Alternative 2: Actor Blocking Shift
    alternatives.append(ProductionAlternative(
        id="alt_blocking_shift",
        category="Actor Blocking",
        title="Shift Dining Table & Actors 0.8m Closer to West Window",
        impact_description=(
            "Moving the table 0.8m towards the window provides 0.8m of additional camera pullback depth without changing the 35mm lens, "
            "while placing Elena directly in the rim light stream."
        ),
        tradeoffs="Tightens space between Elena and the window wall to 1.1m (still within acceptable AD clearance).",
        recommended=False
    ))

    # Alternative 3: Dolly Track Angling
    alternatives.append(ProductionAlternative(
        id="alt_dolly_angle",
        category="Grip & Safety",
        title="Angle Dolly Track 12° North & Terminate 0.5m Earlier",
        impact_description=(
            "Angling the track slightly north clears the 1.0m fire exit buffer and provides 0.4m extra clearance around the gaffer's diffusion frame."
        ),
        tradeoffs="Camera pushes in on a slight diagonal axis, giving a more dynamic perspective on the conversation.",
        recommended=True
    ))

    # Alternative 4: Lighting Continuation
    alternatives.append(ProductionAlternative(
        id="alt_exterior_lighting",
        category="Lighting Continuation",
        title="Rig Exterior High-Output Amber Fixture (e.g. Aputure 600c / 4K HMI)",
        impact_description=(
            "Mount fixture on a stand outside the ground-floor west window to simulate 2800K low-angle sunlight indefinitely, "
            "extending the shooting schedule beyond the 25-minute natural window."
        ),
        tradeoffs="Requires running exterior feeder cable; ensure cable ramp crosses walkway inside venue footprint.",
        recommended=False
    ))

    return alternatives
