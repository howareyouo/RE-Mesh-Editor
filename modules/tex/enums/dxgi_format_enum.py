# -*- coding: utf-8 -*-
# DXGI format strings <-> values, derived from the ddsconv DXGI_FORMAT enum so the
# two copies can never drift apart (the enum is the single source of truth).
from ...ddsconv.directx.dxgi_format import DXGI_FORMAT

formatStringToDXGIDict = {name.replace("_", ""): int(value) for name, value in DXGI_FORMAT.__members__.items()}
# Aliases kept from the original hand-written table (the enum defines them under
# different names or not at all):
formatStringToDXGIDict["DXGIFORMAT420OPAQUE"] = 106  # enum: OPAQUE420
formatStringToDXGIDict["FORCEUINT"] = 4294967295

DXGIToFormatStringDict = {v: k for k, v in formatStringToDXGIDict.items()}
