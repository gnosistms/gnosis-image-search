"""Session-owned buffer for adapters that return a growing result window."""

import copy
from search_runtime import count, check_work


class ProviderCursor:
    def __init__(self):
        self.items = []
        self.requested = 0
        self.exhausted = False
        self.next_offset = 0

    def fetch_pages(self, requested, fetch_page):
        """Consume native pages once, retaining surplus accepted records."""
        check_work()
        if len(self.items) >= requested or self.exhausted:
            count("provider_buffer_hits")
        while len(self.items) < requested and not self.exhausted:
            check_work()
            count("adapter_calls")
            fresh, next_offset, exhausted = fetch_page(self.next_offset, max(20, requested-len(self.items)))
            if next_offset <= self.next_offset and not exhausted:
                raise RuntimeError("Collection cursor did not advance")
            seen = {(i.get("source"), i.get("source_id") or i.get("image_url")) for i in self.items}
            for item in fresh:
                key = (item.get("source"), item.get("source_id") or item.get("image_url"))
                if key not in seen:
                    self.items.append(item)
                    seen.add(key)
            self.next_offset, self.exhausted = next_offset, exhausted
        self.requested = requested
        return copy.deepcopy(self.items)

    def fetch(self, requested, fetch):
        check_work()
        if self.requested and (self.exhausted or len(self.items) >= requested):
            count("provider_buffer_hits")
            return copy.deepcopy(self.items)
        count("adapter_calls")
        fresh = fetch(requested)
        # Preserve already consumed records when a larger provider window has
        # unstable ordering. Add only newly encountered provider identities.
        def identity(item):
            return (item.get("source"), item.get("source_id") or item.get("image_url"))
        seen = {identity(item) for item in self.items}
        count("repeated_provider_records", sum(identity(item) in seen for item in fresh))
        for item in fresh:
            key = identity(item)
            if key not in seen:
                self.items.append(item)
                seen.add(key)
        self.exhausted = len(fresh) < requested
        self.requested = requested
        return copy.deepcopy(self.items)
