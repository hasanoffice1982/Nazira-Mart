import json


def hx_trigger(response, **events):
    """Attach HX-Trigger events; ASCII-escaped JSON keeps non-latin characters header-safe."""
    response["HX-Trigger"] = json.dumps(events, ensure_ascii=True)
    return response
