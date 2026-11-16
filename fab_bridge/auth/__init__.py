"""Epic authentication: token model, platform store, OAuth, session manager.

`models` and `store` import without bpy or requests; `oauth` only touches
requests when a call is actually made.
"""
