# Importing a collector module registers it in the registry.
from auscpi.collectors import abs_cpi, fuelcheck, sqm_rents  # noqa: F401
from auscpi.collectors.base import Collector, CollectorResult, registry

__all__ = ["Collector", "CollectorResult", "registry"]
