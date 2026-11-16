
from __future__ import annotations

from typing import Any


def adapter_registry() -> dict[str, Any]:

    from disaster_v2.sources.ercc import ERCCAdapter
    from disaster_v2.sources.gdacs import GDACSAdapter
    from disaster_v2.sources.usgs import USGSAdapter
    from disaster_v2.sources.who import WHODiseaseOutbreakAdapter

    return {
        "USGS": USGSAdapter(),
        "GDACS": GDACSAdapter(),
        "WHO": WHODiseaseOutbreakAdapter(),
        "ERCC": ERCCAdapter(),
    }
