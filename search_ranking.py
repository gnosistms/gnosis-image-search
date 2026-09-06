"""Prepare duplicate evidence outside the session lock; compare without I/O."""

import math
from ranker import rank_result_groups
from semantic_embeddings import cached_image_vectors
from visual_similarity import cached_item_feature, feature_similarity, likely_same_image


def rank_snapshot(query, items, feature_overrides=None):
    vectors = cached_image_vectors(items)
    features = {item["id"]: cached_item_feature(item) for item in items}
    features.update(feature_overrides or {})
    pending = set()
    affected = set()

    def same_image(first, second):
        a, b = vectors.get(first["id"]), vectors.get(second["id"])
        if a is None or b is None:
            return False
        similarity = float(a @ b)
        if similarity < .9:
            return False
        ratio_a = (first.get("width") or 1) / max(first.get("height") or 1, 1)
        ratio_b = (second.get("width") or 1) / max(second.get("height") or 1, 1)
        if abs(math.log(max(ratio_a, .05) / max(ratio_b, .05))) > .2:
            return False
        ready_a, feature_a = features[first["id"]]
        ready_b, feature_b = features[second["id"]]
        if not ready_a or not ready_b:
            affected.update((first["id"], second["id"]))
            pending.update(item["id"] for item, ready in
                           ((first, ready_a), (second, ready_b)) if not ready)
            return False
        perceptual = (feature_similarity(feature_a, feature_b)
                      if feature_a is not None and feature_b is not None else None)
        return likely_same_image(first, second, similarity, perceptual)

    ranked, families = rank_result_groups(query, items, same_image)
    # Metadata previews are useful to display but cannot displace scored images
    # in a quality decision. Rank the scored members themselves, then map their
    # family to the visible representative (which may have a larger resolution).
    quality = []
    for representative, members in families.items():
        scored = [m for m in members if isinstance(m.get("pamela_score"), (int, float))
                  and m.get("pamela_rerank")]
        if scored:
            best = max(scored, key=lambda m: m["rank_score"])
            quality.append((representative, best))
    quality.sort(key=lambda pair: (-pair[1]["rank_score"], pair[1].get("provider_rank", 0),
                                   pair[1].get("source_label", ""), pair[1].get("title", "")))
    return ranked, families, {item_id: n+1 for n, (item_id, _) in enumerate(quality)}, pending, affected
