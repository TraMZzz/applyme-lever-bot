"""Shared constant: the Lever hCaptcha sitekey.

Kept in its own module to break the base↔capsolver import cycle.
`base.SITEKEY` remains the public name (base re-exports this).
"""

SITEKEY = "e33f87f8-88ec-4e1a-9a13-df9bbb1d8120"
