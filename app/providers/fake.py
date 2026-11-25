"""Scripted provider for offline tests. script: dict person_id -> ProviderResult | Exception, or callable."""
from __future__ import annotations
from app.providers.base import Provider, ProviderResult


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, script=None, configured=True):
        self.script, self._configured, self.calls = script or {}, configured, []

    def configured(self):
        return self._configured

    def lookup(self, person):
        self.calls.append(person["id"])
        r = self.script(person) if callable(self.script) else self.script.get(person["id"], ProviderResult("not_found"))
        if isinstance(r, Exception):
            raise r
        return r
