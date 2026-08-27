import json
import math
import threading
import time
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

import ranker
import server
import visual_similarity
import sources
import keys
import gnosis_fuzzy
import additional_sources
import gnosis_catalog
import semantic_embeddings


class StaticFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), server.SearchHandler, max_workers=2,
        )
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(2)

    def test_hero_artwork_is_served_as_jpeg(self):
        artwork = (
            "annunciation-1390.jpg",
            "flight-into-egypt.jpg",
            "madonna-and-child-with-musical-angels.jpg",
            "adoration-of-the-magi.jpg",
        )
        for filename in artwork:
            with self.subTest(filename=filename):
                with urllib.request.urlopen(
                    f"{self.base_url}/images/getty/{filename}"
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "image/jpeg")
                    self.assertTrue(response.read(3).startswith(b"\xff\xd8\xff"))

    def test_unlisted_static_path_is_not_exposed(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base_url}/images/getty/not-present.jpg")
        self.assertEqual(raised.exception.code, 404)


def result(source, title="Result", source_id="1", **extra):
    item = {
        "source": source,
        "source_id": source_id,
        "title": title,
        "license": "CC0",
        "page_url": f"https://example.test/{source}/{source_id}",
        "image_url": f"https://images.test/{source}/{source_id}.jpg",
        "thumb_url": f"https://images.test/{source}/{source_id}-thumb.jpg",
        "width": 1200,
        "height": 800,
    }
    item.update(extra)
    return item


class ExactPhraseTests(unittest.TestCase):
    def test_normalization_exposes_bounded_query_bearing_search_text(self):
        item = result(
            "wellcome", "A vessel",
            description="A generic catalog description.",
            search_text=[
                "Unrelated note.",
                "The lettering identifies the Guardian of the Threshold.",
            ],
        )
        normalized = server.normalize_result(
            item, query="guardian of the threshold",
        )
        self.assertEqual(
            normalized["match_context"],
            "The lettering identifies the Guardian of the Threshold.",
        )
        self.assertIn("Provider metadata", normalized["matched_fields"])
        self.assertNotIn("search_text", normalized)

    def test_normalization_marks_provider_hit_without_visible_evidence(self):
        normalized = server.normalize_result(
            result("loc", "Unrelated record"), query="caduceus",
        )
        self.assertEqual(normalized["match_context"], "")
        self.assertEqual(
            normalized["match_evidence_status"],
            "provider_returned_without_visible_evidence",
        )

    def test_normalization_keeps_narrative_and_match_evidence_separate(self):
        normalized = server.normalize_result(result(
            "aic", "Mary Magdalene",
            description="A woman looks toward the viewer.",
            search_text={"Controlled term": ["tree of life"]},
        ), query="tree of life")
        self.assertEqual(
            normalized["description"], "A woman looks toward the viewer."
        )
        self.assertEqual(normalized["match_context"], "tree of life")
        self.assertEqual(normalized["matched_fields"], ["Controlled Term"])

    def test_toggle_treats_unquoted_query_as_one_phrase(self):
        parsed = server.parse_search_query("Guardian of the Threshold", True)
        self.assertEqual(parsed.retrieval, "Guardian of the Threshold")
        self.assertEqual(parsed.exact_phrases, ("Guardian of the Threshold",))

    def test_quotes_define_multiple_required_phrases_without_toggle(self):
        parsed = server.parse_search_query('"text one" "text two"')
        self.assertFalse(parsed.exact_requested)
        self.assertEqual(parsed.retrieval, "text one text two")
        self.assertEqual(parsed.exact_phrases, ("text one", "text two"))

    def test_toggle_adds_unquoted_text_to_quoted_phrases(self):
        parsed = server.parse_search_query('angel "holy spirit" wings', True)
        self.assertEqual(parsed.exact_phrases, ("holy spirit", "angel wings"))

    def test_unclosed_quote_is_rejected(self):
        with self.assertRaisesRegex(server.InputError, "Close the quotation"):
            server.parse_search_query('"guardian of the threshold')

    def test_phrase_matching_ignores_case_punctuation_and_whitespace(self):
        item = result(
            "met", "Object", description="The GUARDIAN—of\n the   threshold waits.",
        )
        self.assertTrue(server.result_matches_exact_phrases(
            item, ("guardian of the threshold",),
        ))

    def test_multiple_phrases_can_match_in_any_order_and_different_fields(self):
        item = result(
            "met", "Text Two", description="An example of text one here.",
        )
        self.assertTrue(server.result_matches_exact_phrases(
            item, ("text one", "text two"),
        ))

    def test_phrase_matching_rejects_intervening_words_and_field_crossing(self):
        self.assertFalse(server.result_matches_exact_phrases(
            result("met", "Guardian", description="of the threshold"),
            ("guardian of the threshold",),
        ))
        self.assertFalse(server.result_matches_exact_phrases(
            result("met", "Guardian great of the threshold"),
            ("guardian of the threshold",),
        ))

    def test_exact_batch_filters_candidates_before_paging(self):
        candidates = [
            result("met", "A guardian far from the threshold", "0"),
            result("met", "Guardian of the Threshold", "1"),
            result("met", "Copy: guardian—of the threshold", "2"),
        ]
        calls = []

        def adapter(query, need):
            calls.append((query, need))
            return candidates

        group = server.search_batch(
            "met", "guardian of the threshold", 0, 1,
            {"met": adapter}, resolve_dimensions=False,
            exact_phrases=("guardian of the threshold",),
        )
        self.assertEqual(calls, [("guardian of the threshold", 40)])
        self.assertEqual([item["source_id"] for item in group["results"]], ["1"])
        self.assertTrue(group["exact_verified"])
        self.assertTrue(group["exhausted"])

    def test_rijksmuseum_compound_exact_search_intersects_phrase_results(self):
        calls = []
        candidates = {
            "text one": [
                result("rijksmuseum", "Text one and text two", "both"),
                result("rijksmuseum", "Text one only", "first"),
            ],
            "text two": [
                result("rijksmuseum", "Text one and text two", "both"),
                result("rijksmuseum", "Text two only", "second"),
            ],
        }

        def adapter(query, need):
            calls.append((query, need))
            return candidates[query]

        group = server.search_batch(
            "rijksmuseum", "text one text two", 0, 10,
            {"rijksmuseum": adapter}, resolve_dimensions=False,
            exact_phrases=("text one", "text two"),
        )
        self.assertEqual([query for query, _need in calls], ["text one", "text two"])
        self.assertEqual([item["source_id"] for item in group["results"]], ["both"])

    def test_wellcome_receives_the_exact_phrase_candidate_strategy(self):
        calls = []

        def adapter(query, need, *, exact_phrases=()):
            calls.append((query, need, exact_phrases))
            return [result(
                "wellcome", "A vessel", "wellcome-exact",
                search_text=["Guardian—of the threshold"],
            )]

        group = server.search_batch(
            "wellcome", "guardian of the threshold", 0, 1,
            {"wellcome": adapter}, resolve_dimensions=False,
            exact_phrases=("guardian of the threshold",),
        )
        self.assertEqual(calls, [(
            "guardian of the threshold", 40,
            ("guardian of the threshold",),
        )])
        self.assertEqual(
            [item["source_id"] for item in group["results"]],
            ["wellcome-exact"],
        )

    def test_session_snapshot_exposes_exact_search_contract(self):
        session = server.create_session('"text one" "text two"', ["met"])
        snapshot = session.snapshot()
        self.assertTrue(snapshot["exact_active"])
        self.assertFalse(snapshot["exact_requested"])
        self.assertEqual(snapshot["exact_phrases"], ["text one", "text two"])


class RankingTests(unittest.TestCase):
    def test_exact_title_match_beats_provider_rank(self):
        exact = server.normalize_result(result("met", "Sophia divine wisdom"), 8)
        weak = server.normalize_result(result("gnosis", "Portrait of Sophia"), 0)
        ranked = ranker.rank_results("Sophia divine wisdom", [weak, exact])
        self.assertEqual(ranked[0]["id"], exact["id"])

    def test_gnosis_boost_breaks_a_near_tie_only(self):
        gnosis = server.normalize_result(result("gnosis", "Rose cross"), 1)
        museum = server.normalize_result(result("met", "Rose cross", source_id="2"), 1)
        ranked = ranker.rank_results("rose cross", [museum, gnosis])
        self.assertEqual(ranked[0]["source"], "gnosis")

    def test_action_synonym_outweighs_a_generic_subject_match(self):
        liberation = server.normalize_result(
            result("aic", "Liberation of Saint Peter from Prison"), 3
        )
        generic = server.normalize_result(
            result("wellcome", "Saint Peter appearing to Saint Agatha in prison"), 0
        )
        ranked = ranker.rank_results("Saint Peter escaping prison", [generic, liberation])
        self.assertEqual(ranked[0]["id"], liberation["id"])

    def test_duplicate_image_urls_are_collapsed(self):
        first = server.normalize_result(result("gnosis", "Work"), 0)
        duplicate = server.normalize_result(result("met", "Work", source_id="2"), 0)
        duplicate["image_url"] = first["image_url"] + "?size=large"
        ranked = ranker.rank_results("work", [first, duplicate])
        self.assertEqual(len(ranked), 1)

    def test_long_duplicate_artwork_titles_are_collapsed_across_urls(self):
        title = "Saint Peter in prison visited by Saint Paul after Filippino Lippi"
        first = server.normalize_result(result("wellcome", title, "1"), 0)
        second = server.normalize_result(result("openverse", title, "2"), 0)
        self.assertEqual(len(ranker.rank_results("Saint Peter prison", [first, second])), 1)

    def test_family_keeps_highest_resolution_and_retains_alternate(self):
        small = server.normalize_result(result("met", "A work", "1", width=800, height=600))
        large = server.normalize_result(result("nga", "Another record", "2", width=2400, height=1800))
        ranked, families = ranker.rank_result_groups(
            "work", [small, large], same_image=lambda first, second: True,
        )
        self.assertEqual(ranked[0]["id"], large["id"])
        self.assertEqual(ranked[0]["duplicate_count"], 1)
        self.assertEqual({item["id"] for item in families[large["id"]]},
                         {small["id"], large["id"]})

    def test_provider_primary_view_beats_larger_alternate_from_same_work(self):
        front = server.normalize_result({
            **result("wellcome", "A postcard", "front", width=1000, height=700),
            "work_id": "work-1", "is_primary_view": True,
        }, 1)
        back = server.normalize_result({
            **result("wellcome", "A postcard", "back", width=3000, height=2000),
            "work_id": "work-1", "is_primary_view": False,
        }, 0)
        ranked, families = ranker.rank_result_groups("postcard", [back, front])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["source_id"], "front")
        self.assertEqual(len(families[ranked[0]["id"]]), 2)

    def test_gnosis_variants_with_same_curated_description_are_collapsed(self):
        description = "A detailed curated description of the same historical image " * 3
        original = server.normalize_result(
            result("gnosis", "Original scan", "1", medium=description), 0,
        )
        upscale = server.normalize_result(
            result("gnosis", "Topaz upscale", "2", medium=description,
                   width=2400, height=1600), 1,
        )
        ranked = ranker.rank_results("historical image", [original, upscale])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["id"], upscale["id"])

    def test_rank_is_log2_pixel_area_times_siglip_relevance(self):
        item = server.normalize_result(
            result("nga", "Object", "2", width=2000, height=500), 0,
        )
        item["semantic_score"] = 0.75
        score, reason = ranker.score_result("subject", item)
        self.assertAlmostEqual(score, math.log2(2000 * 500) * 0.75, places=5)
        self.assertIn(f"size {math.log2(2000 * 500):.2f}", reason)
        self.assertIn("relevance 0.750", reason)

    def test_resolution_and_relevance_both_affect_rank(self):
        large = server.normalize_result(
            result("met", "Object", "1", width=2048, height=2048), 0,
        )
        relevant = server.normalize_result(
            result("nga", "Object", "2", width=1024, height=1024), 0,
        )
        large["semantic_score"] = 0.25
        relevant["semantic_score"] = 0.75
        ranked = ranker.rank_results("subject", [large, relevant])
        self.assertEqual(ranked[0]["id"], relevant["id"])
        self.assertEqual(ranked[0]["size_score"], 20.0)
        self.assertEqual(ranked[0]["relevance_score"], 0.75)

    def test_pamela_mode_ranks_as_size_times_criterion_score(self):
        preferred = server.normalize_result(result("met", "Preferred", "1"), 0)
        rejected = server.normalize_result(result("met", "Rejected", "2"), 0)
        for item, criterion in ((preferred, 1.0), (rejected, 0.0)):
            item["semantic_score"] = 0.5
            item["pamela_score"] = criterion
            item["pamela_rerank"] = True
        ranked = ranker.rank_results("subject", [rejected, preferred])
        self.assertEqual(ranked[0]["id"], preferred["id"])
        self.assertIn("PAMELA criteria", ranked[0]["rank_reason"])
        expected = math.log2(1200 * 800)
        self.assertAlmostEqual(ranked[0]["rank_score"], expected, places=4)


class SessionTests(unittest.TestCase):
    def setUp(self):
        with server.SESSIONS_LOCK:
            server.SESSIONS.clear()

    def test_gnosis_is_optional_and_selectable(self):
        self.assertEqual(server.parse_sources("met,gnosis"), ["met", "gnosis"])
        self.assertEqual(server.parse_sources("met"), ["met"])

    def test_universal_comasonry_is_a_selectable_collection(self):
        self.assertEqual(
            server.parse_sources("universal_comasonry"),
            ["universal_comasonry"],
        )

    def test_every_collection_is_selected_by_default(self):
        self.assertEqual(tuple(server.SOURCE_LABELS), server.DEFAULT_SELECTED)

    def test_session_merges_sources_into_one_ranked_list(self):
        session = server.create_session("divine wisdom", ["gnosis", "met"])
        gnosis_item = server.normalize_result(result("gnosis", "Book collection"), 0)
        met_item = server.normalize_result(result("met", "Divine Wisdom", source_id="2"), 3)
        session.merge_batch({"source": "gnosis", "results": [gnosis_item], "error": "",
                             "offset": 0, "exhausted": True})
        snapshot = session.merge_batch({"source": "met", "results": [met_item], "error": "",
                                        "offset": 0, "exhausted": True})
        self.assertEqual(len(snapshot["results"]), 2)
        self.assertEqual(snapshot["results"][0]["source"], "met")
        self.assertEqual(snapshot["revision"], 2)

    def test_session_gallery_collapses_but_keeps_versions_addressable(self):
        session = server.create_session("work", ["met"])
        small = server.normalize_result(result("met", "Work", "1", width=800, height=600))
        large = server.normalize_result(result("met", "Work", "2", width=2400, height=1800))
        large["image_url"] = small["image_url"] + "?download=1"
        snapshot = session.merge_batch({
            "source": "met", "results": [small, large], "error": "",
            "offset": 0, "count": 2, "exhausted": True,
        })
        self.assertEqual(len(snapshot["results"]), 1)
        self.assertEqual(snapshot["results"][0]["id"], large["id"])
        self.assertEqual(session.item(small["id"])["id"], small["id"])
        self.assertEqual({item["id"] for item in session.family(small["id"])},
                         {small["id"], large["id"]})
        related = session.related(large["id"])
        self.assertEqual([item["id"] for item in related["alternates"]], [small["id"]])
        self.assertEqual(related["results"], [])

    def test_later_stronger_results_move_to_the_top(self):
        session = server.create_session("saint peter prison", ["gnosis", "met"])
        early = server.normalize_result(result("gnosis", "Portrait of Saint Peter"), 0)
        first = session.merge_batch({"source": "gnosis", "results": [early], "error": "",
                                     "offset": 0, "exhausted": True})
        self.assertEqual(first["results"][0]["id"], early["id"])

        late = server.normalize_result(
            result("met", "Saint Peter escaping prison", source_id="2"), 4
        )
        second = session.merge_batch({"source": "met", "results": [late], "error": "",
                                      "offset": 0, "exhausted": True})
        self.assertEqual(second["results"][0]["id"], late["id"])
        self.assertGreater(second["revision"], first["revision"])

    def test_http_merges_coalesce_ranking_until_policy_snapshot(self):
        session = server.create_session("divine wisdom", ["gnosis", "met"])
        first_item = server.normalize_result(result("gnosis", "Book collection"), 0)
        second_item = server.normalize_result(
            result("met", "Divine Wisdom", source_id="2"), 0,
        )
        first = session.merge_batch({
            "source": "gnosis", "results": [first_item], "error": "",
            "offset": 0, "count": 1, "exhausted": True,
        }, coalesce_ranking=True)
        second = session.merge_batch({
            "source": "met", "results": [second_item], "error": "",
            "offset": 0, "count": 1, "exhausted": True,
        }, coalesce_ranking=True)
        self.assertEqual(len(first["results"]), 1)
        self.assertEqual(len(second["results"]), 1)
        final = session.snapshot(force_rank=True)
        self.assertEqual(len(final["results"]), 2)
        self.assertEqual(final["results"][0]["id"], second_item["id"])

    def test_cancelled_session_rejects_late_merge(self):
        session = server.create_session("light", ["met"])
        session.cancel()
        with self.assertRaises(server.SearchCancelled):
            session.merge_batch({
                "source": "met", "results": [], "error": "",
                "offset": 0, "count": 0, "exhausted": True,
            })

    def test_source_cancellation_does_not_cancel_other_collections(self):
        session = server.create_session("light", ["met", "nga"])
        session.cancel_source("met")
        self.assertTrue(session.source_cancelled("met"))
        self.assertFalse(session.source_cancelled("nga"))

    def test_provider_failure_is_isolated(self):
        def fail(query, limit):
            raise RuntimeError("provider unavailable")

        group = server.search_one("met", "light", 4, {"met": fail})
        self.assertEqual(group["results"], [])
        self.assertIn("provider unavailable", group["error"])

    def test_adaptive_policy_stops_a_source_whose_latest_ten_miss_top_50(self):
        session = server.create_session("target subject", ["strong", "weak"])
        session.source_states = {
            "strong": {"fetched": 0, "last_ids": [], "exhausted": False,
                       "stop_reason": "", "rounds": 0, "last_batch_count": 0},
            "weak": {"fetched": 0, "last_ids": [], "exhausted": False,
                     "stop_reason": "", "rounds": 0, "last_batch_count": 0},
        }
        strong = [server.normalize_result(
            result("met", "target subject", str(index)), index
        ) for index in range(50)]
        weak = [server.normalize_result(
            result("openverse", f"unrelated item {index}", str(index)), index
        ) for index in range(10)]
        session.results = {item["id"]: item for item in ranker.rank_results(
            session.query, strong + weak
        )}
        session.source_states["strong"].update(
            fetched=50, last_ids=[item["id"] for item in strong[-10:]], rounds=5,
            last_batch_count=10
        )
        session.source_states["weak"].update(
            fetched=10, last_ids=[item["id"] for item in weak], rounds=1,
            last_batch_count=10
        )
        policy = session.continuation_policy()
        self.assertFalse(policy["weak"]["continue"])
        self.assertIn("no aggregate top-50", policy["weak"]["reason"])

    def test_adaptive_policy_continues_when_latest_batch_has_a_top_50_hit(self):
        session = server.create_session("target", ["met"])
        batch = [server.normalize_result(
            result("met", "target", str(index)), index
        ) for index in range(10)]
        session.results = {item["id"]: item for item in ranker.rank_results("target", batch)}
        session.source_states["met"].update(
            fetched=10, last_ids=[item["id"] for item in batch], rounds=1,
            last_batch_count=10
        )
        self.assertTrue(session.continuation_policy()["met"]["continue"])

    def test_unqueried_source_is_reported_as_still_active(self):
        session = server.create_session("target", ["met"])
        policy = session.continuation_policy()["met"]
        self.assertTrue(policy["continue"])
        self.assertEqual(policy["reason"], "awaiting first batch")

    def test_validation(self):
        self.assertEqual(server.validate_query("  divine   light  "), "divine light")
        with self.assertRaises(server.InputError):
            server.parse_sources("unknown")
        with self.assertRaises(server.InputError):
            server.parse_sources("openverse")
        self.assertEqual(server.parse_sources("commons"), ["commons"])
        with self.assertRaises(server.InputError):
            server.validate_limit(0)

    def test_aic_proxy_resolves_only_approved_session_images(self):
        session = server.create_session("peter", ["aic"])
        proxied = server.normalize_result(result(
            "aic", source_id="1", image_delivery="huggingface",
            image_url="https://datasets-server.huggingface.co/cached/image.jpg",
        ))
        direct = server.normalize_result(result(
            "aic", source_id="2", image_delivery="commons",
            image_url="https://commons.wikimedia.org/image.jpg",
        ))
        session.results = {item["id"]: item for item in (proxied, direct)}
        self.assertEqual(
            server.get_aic_proxy_item(session.id, proxied["id"])["source_id"], "1"
        )
        with self.assertRaises(server.InputError):
            server.get_aic_proxy_item(session.id, direct["id"])
        with self.assertRaises(server.InputError):
            server.get_aic_proxy_item(session.id, "missing")

    def test_aic_proxy_rejects_unapproved_host(self):
        session = server.create_session("peter", ["aic"])
        item = server.normalize_result(result(
            "aic", image_delivery="huggingface",
            image_url="https://example.test/not-a-mirror.jpg",
        ))
        session.results[item["id"]] = item
        with self.assertRaises(server.InputError):
            server.get_aic_proxy_item(session.id, item["id"])

    def test_aic_proxy_supplies_jpeg_mime_and_retries(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"image"
        calls = []
        original_urlopen = server.urllib.request.urlopen
        original_sleep = server.time.sleep

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return jpeg

        def urlopen(request, timeout):
            calls.append((request.full_url, timeout))
            if len(calls) == 1:
                raise urllib.error.URLError("temporary")
            return Response()

        server.urllib.request.urlopen = urlopen
        server.time.sleep = lambda seconds: None
        try:
            data, mime = server.fetch_aic_preview(
                "https://datasets-server.huggingface.co/cached/image.jpg"
            )
        finally:
            server.urllib.request.urlopen = original_urlopen
            server.time.sleep = original_sleep
        self.assertEqual(data, jpeg)
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(len(calls), 2)

    def test_aic_proxy_rejects_non_image_payload(self):
        with self.assertRaises(server.ImageProxyError):
            server.image_mime_type(b"not an image")

    def test_harvard_proxy_recovers_from_transient_rate_limits(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"image"
        image_calls = []
        cookie_calls = []
        sleeps = []
        original_urlopen = server.urllib.request.urlopen
        original_cookie = server.harvard_cookie
        original_sleep = server.time.sleep

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return jpeg

        def urlopen(request, timeout):
            image_calls.append(request.full_url)
            if len(image_calls) < 3:
                raise urllib.error.HTTPError(
                    request.full_url, 429, "rate limited", {}, None,
                )
            return Response()

        def cookie(page_url, force=False):
            cookie_calls.append(force)
            return "signed=cookie"

        server.urllib.request.urlopen = urlopen
        server.harvard_cookie = cookie
        server.time.sleep = sleeps.append
        try:
            data, mime = server.fetch_harvard_preview({
                "thumb_url": (
                    "https://nrs.harvard.edu/urn-3:HUAM:ABC_dynmc/"
                    "full/!1024,1024/0/default.jpg"
                ),
                "page_url": (
                    "https://www.harvardartmuseums.org/collections/object/42"
                ),
            })
        finally:
            server.urllib.request.urlopen = original_urlopen
            server.harvard_cookie = original_cookie
            server.time.sleep = original_sleep

        self.assertEqual((data, mime), (jpeg, "image/jpeg"))
        self.assertEqual(len(image_calls), 3)
        self.assertEqual(cookie_calls, [False, True, False])
        self.assertEqual(sleeps, [0.5, 1.0])


class BatchTests(unittest.TestCase):
    def test_met_does_not_fall_back_when_title_search_is_empty(self):
        calls = []
        original_get_json = sources._get_json

        def fake_get_json(url, source, **kwargs):
            calls.append((url, source))
            return {"total": 0, "objectIDs": None}

        sources._get_json = fake_get_json
        try:
            self.assertEqual(sources.met("kundalini", 10), [])
        finally:
            sources._get_json = original_get_json

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "met")
        self.assertIn("title=true", calls[0][0])

    def test_new_collections_are_registered_and_selected(self):
        self.assertEqual(server.SOURCE_LABELS["getty"], "J. Paul Getty Museum")
        self.assertEqual(server.SOURCE_LABELS["loc"], "Library of Congress")
        self.assertEqual(server.SOURCE_LABELS["nga"], "National Gallery of Art")
        self.assertEqual(server.SOURCE_LABELS["harvard"], "Harvard Art Museums")
        self.assertEqual(
            server.SOURCE_LABELS["commons"],
            "Wikimedia Commons — Museum Collections",
        )
        self.assertEqual(server.SOURCE_LABELS["yale"], "Yale LUX — Art Museums")
        self.assertEqual(server.SOURCE_LABELS["paris_musees"], "Paris Musées")
        self.assertEqual(
            server.SOURCE_LABELS["mia"], "Minneapolis Institute of Art"
        )
        self.assertEqual(
            server.SOURCE_LABELS["universal_comasonry"],
            "Universal Co-Masonry Galleries",
        )
        for name in (
            "getty", "loc", "nga", "harvard", "yale", "paris_musees",
            "mia", "commons", "universal_comasonry",
        ):
            self.assertIn(name, sources.ADAPTERS)
            self.assertIn(name, server.DEFAULT_SELECTED)

        self.assertEqual(len(server.SOURCE_LABELS), 18)
        self.assertEqual(server.GOOGLE_IMAGE_SOURCES, frozenset((
            "universal_comasonry", "cleveland", "met", "aic", "nga",
            "harvard", "mia", "loc", "wellcome", "vam", "commons",
        )))
        self.assertTrue(server.GOOGLE_IMAGE_SOURCES <= set(server.SOURCE_LABELS))
        self.assertTrue(server.GOOGLE_IMAGE_SOURCES <= set(server.SOURCE_DOMAINS))
        for name in (
            "gnosis", "rijksmuseum", "getty", "yale", "paris_musees",
            "smk", "europeana",
        ):
            self.assertNotIn(name, server.GOOGLE_IMAGE_SOURCES)
        self.assertNotIn("getty_alchemy", server.SOURCE_LABELS)
        self.assertNotIn("getty_alchemy", sources.ADAPTERS)
        self.assertNotIn("getty_alchemy", server.DEFAULT_SELECTED)
        self.assertNotIn("smithsonian", server.SOURCE_LABELS)
        self.assertNotIn("smithsonian", sources.ADAPTERS)
        self.assertEqual(server.SOURCE_LABELS["europeana"], "Europeana")
        self.assertIn("europeana", sources.ADAPTERS)
        self.assertIn("europeana", server.DEFAULT_SELECTED)

    def test_europeana_key_is_sent_in_header_not_url(self):
        secret = "test-europeana-key"
        calls = []
        original_get_key = keys.get_key
        original_get_json = sources._get_json

        def fake_get_json(url, source, **kwargs):
            calls.append((url, source, kwargs))
            return {"items": []}

        keys.get_key = lambda name: secret if name == "europeana" else ""
        sources._get_json = fake_get_json
        try:
            self.assertEqual(sources.europeana("saint peter", 10), [])
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json

        url, source, kwargs = calls[0]
        self.assertEqual(source, "europeana")
        self.assertNotIn(secret, url)
        self.assertNotIn("wskey", urllib.parse.parse_qs(urllib.parse.urlparse(url).query))
        self.assertEqual(kwargs["headers"], {"X-Api-Key": secret})

    def test_europeana_rate_limit_becomes_an_access_error(self):
        original_urlopen = sources.urllib.request.urlopen

        def reject(_request, timeout=0):
            raise urllib.error.HTTPError(
                "https://api.europeana.eu/record/v2/search.json",
                429, "Too Many Requests", {}, None,
            )

        sources.urllib.request.urlopen = reject
        try:
            with self.assertRaises(sources.EuropeanaAccessError) as raised:
                sources._get_json(
                    "https://api.europeana.eu/record/v2/search.json?query=limit-test",
                    "europeana", ttl_ok=False,
                )
        finally:
            sources.urllib.request.urlopen = original_urlopen
        self.assertEqual(raised.exception.status, 429)

    def test_europeana_access_error_is_exposed_as_a_safe_ui_alert(self):
        def blocked(_query, _need):
            raise sources.EuropeanaAccessError(429)

        group = server.search_batch(
            "europeana", "saint peter", 0,
            adapters={"europeana": blocked},
        )
        self.assertEqual(group["alert"], {
            "code": "europeana_key_access", "status": 429,
        })
        session = server.SearchSession("saint peter", ["europeana"])
        snapshot = session.merge_batch(group)
        self.assertEqual(
            snapshot["source_alerts"]["europeana"]["code"],
            "europeana_key_access",
        )

    def test_europeana_json_authentication_failure_becomes_an_access_error(self):
        original_get_key = keys.get_key
        original_get_json = sources._get_json
        keys.get_key = lambda name: "test-key"
        sources._get_json = lambda *args, **kwargs: {
            "success": False, "error": "Invalid API key",
        }
        try:
            with self.assertRaises(sources.EuropeanaAccessError):
                sources.europeana("saint peter", 10)
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json

    def test_europeana_non_access_json_error_does_not_show_key_warning(self):
        original_get_key = keys.get_key
        original_get_json = sources._get_json
        keys.get_key = lambda name: "test-key"
        sources._get_json = lambda *args, **kwargs: {
            "success": False, "error": "Invalid query syntax",
        }
        try:
            self.assertEqual(sources.europeana("(", 10), [])
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json

    def test_europeana_preserves_english_description(self):
        original_get_key = keys.get_key
        original_get_json = sources._get_json
        keys.get_key = lambda name: "test-key"
        sources._get_json = lambda *args, **kwargs: {"items": [{
            "id": "/item/1", "title": ["Work"],
            "edmIsShownBy": ["https://images.test/work.jpg"],
            "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"],
            "dcDescriptionLangAware": {
                "de": ["Deutsche Beschreibung"],
                "en": ["An angel approaches a seated woman."],
            },
        }]}
        try:
            item = sources.europeana("angel", 1)[0]
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json
        self.assertEqual(item["description"], "An angel approaches a seated woman.")

    def test_europeana_excludes_pdf_misclassified_as_image(self):
        original_get_key = keys.get_key
        original_get_json = sources._get_json
        keys.get_key = lambda name: "test-key" if name == "europeana" else ""
        sources._get_json = lambda *args, **kwargs: {"items": [{
            "id": "/234/_nnRgRl3",
            "title": ["Atlantis. Drama"],
            "dataProvider": ["Silesian Digital Library"],
            "edmIsShownBy": [
                "https://sbc.org.pl/Content/253780/ii641596-0000-00-0001.pdf",
            ],
            "edmPreview": ["https://images.test/atlantis.jpg"],
            "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"],
        }]}
        try:
            self.assertEqual(sources.europeana("Atlantis", 1), [])
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json

    def test_europeana_excludes_authoritative_nhm_register_media(self):
        original_get_key = keys.get_key
        original_get_json = sources._get_json
        asset_id = "7dd12720-fbc5-4959-a5ca-9f475c918d20"
        search_item = {
            "id": "/854/NHMUKXZOOX1951X2X17X197X226",
            "title": ["Lucifer"],
            "dataProvider": [
                "The Trustees of the Natural History Museum, London",
            ],
            "edmIsShownBy": [f"https://data.nhm.ac.uk/media/{asset_id}"],
            "rights": ["http://creativecommons.org/licenses/by/4.0/"],
        }

        def fake_get_json(url, _source, **_kwargs):
            if "datastore_search" not in url:
                return {"items": [search_item]}
            return {"result": {"records": [{"associatedMedia": [{
                "assetID": asset_id,
                "title": "Zoology Accessions Register: page 101",
                "category": "Register",
                "type": "StillImage",
            }]}]}}

        keys.get_key = lambda name: "test-key" if name == "europeana" else ""
        sources._get_json = fake_get_json
        try:
            self.assertEqual(sources.europeana("Lucifer", 1), [])
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json

    def test_europeana_keeps_nhm_specimen_media(self):
        original_get_key = keys.get_key
        original_get_json = sources._get_json
        asset_id = "90ed46e5-d649-43c5-8e7b-8a9a1d12c108"
        search_item = {
            "id": "/883/NHMUKXZOOXTEST",
            "title": ["Euzetes globulus Nicolet, 1855"],
            "dataProvider": [
                "The Trustees of the Natural History Museum, London",
            ],
            "edmIsShownBy": [f"https://data.nhm.ac.uk/media/{asset_id}"],
            "rights": ["http://creativecommons.org/licenses/by/4.0/"],
        }

        def fake_get_json(url, _source, **_kwargs):
            if "datastore_search" not in url:
                return {"items": [search_item]}
            return {"result": {"records": [{"associatedMedia": [{
                "assetID": asset_id,
                "category": "Specimen",
                "type": "StillImage",
            }]}]}}

        keys.get_key = lambda name: "test-key" if name == "europeana" else ""
        sources._get_json = fake_get_json
        try:
            items = sources.europeana("mite", 1)
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Euzetes globulus Nicolet, 1855")

    def test_europeana_keeps_nhm_media_when_category_lookup_fails(self):
        original_get_key = keys.get_key
        original_get_json = sources._get_json
        asset_id = "90ed46e5-d649-43c5-8e7b-8a9a1d12c108"
        search_item = {
            "id": "/883/NHMUKXZOOXTEST",
            "title": ["Specimen"],
            "dataProvider": [
                "The Trustees of the Natural History Museum, London",
            ],
            "edmIsShownBy": [f"https://data.nhm.ac.uk/media/{asset_id}"],
        }

        def fake_get_json(url, _source, **_kwargs):
            return None if "datastore_search" in url else {"items": [search_item]}

        keys.get_key = lambda name: "test-key" if name == "europeana" else ""
        sources._get_json = fake_get_json
        try:
            items = sources.europeana("specimen", 1)
        finally:
            keys.get_key = original_get_key
            sources._get_json = original_get_json
        self.assertEqual(len(items), 1)

    def test_cleveland_preserves_curatorial_description(self):
        response = {"data": [{
            "id": 1, "title": "Work", "share_license_status": "CC0",
            "description": "<p>A figure emerges from a dark interior.</p>",
            "images": {"web": {"url": "https://images.test/work.jpg"}},
        }]}
        original = sources._get_json
        sources._get_json = lambda *args, **kwargs: response
        try:
            item = sources.cleveland("figure", 1)[0]
        finally:
            sources._get_json = original
        self.assertEqual(item["description"], "A figure emerges from a dark interior.")

    def test_cleveland_quoted_query_is_locally_phrase_filtered(self):
        response = {"info": {"total": 3}, "data": [
            {
                "id": 1, "title": "Tea and Coffee Service",
                "description": "The tea and service pieces are displayed together.",
                "images": {"web": {"url": "https://images.test/false.jpg"}},
            },
            {
                "id": 2, "title": "Tea Service",
                "images": {"web": {"url": "https://images.test/title.jpg"}},
            },
            {
                "id": 3, "title": "Vessel",
                "description": "An elaborate TEA\u00a0\n service for a household.",
                "images": {"web": {"url": "https://images.test/description.jpg"}},
            },
        ]}
        urls = []
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: (
            urls.append(url) or response
        )
        try:
            items = sources.cleveland('"tea service"', 10)
        finally:
            sources._get_json = original
        self.assertEqual([item["source_id"] for item in items], ["2", "3"])
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
        self.assertEqual(params["q"], ["tea service"])
        self.assertEqual(params["limit"], ["1000"])
        self.assertEqual(params["smart_parts"], ["1"])

    def test_cleveland_exact_phrase_does_not_cross_metadata_fields(self):
        artwork = {"title": "Tea", "description": "Service for six."}
        self.assertFalse(sources._cleveland_has_exact_phrase(
            artwork, "tea service",
        ))

    def test_cleveland_exact_phrase_ignores_punctuation_space_and_case(self):
        artwork = {"title": "A TEA—SERVICE: for Six"}
        self.assertTrue(sources._cleveland_has_exact_phrase(
            artwork, "tea service",
        ))

    def test_cleveland_exact_phrase_rejects_intervening_words(self):
        artwork = {"title": "A tea and coffee service"}
        self.assertFalse(sources._cleveland_has_exact_phrase(
            artwork, "tea service",
        ))

    def test_cleveland_exact_phrase_preserves_word_boundaries(self):
        artwork = {"title": "A teaservice for six"}
        self.assertFalse(sources._cleveland_has_exact_phrase(
            artwork, "tea service",
        ))

    def test_cleveland_exact_phrase_paginates_all_candidates(self):
        false_matches = [{
            "id": index, "title": "Tea and Coffee Service",
            "images": {"web": {"url": f"https://images.test/{index}.jpg"}},
        } for index in range(1000)]
        exact = {
            "id": 1001, "title": "Tea Service",
            "images": {"web": {"url": "https://images.test/exact.jpg"}},
        }
        urls = []
        original = sources._get_json

        def paged(url, *args, **kwargs):
            urls.append(url)
            skip = int(urllib.parse.parse_qs(
                urllib.parse.urlsplit(url).query
            )["skip"][0])
            return {
                "info": {"total": 1001},
                "data": false_matches if skip == 0 else [exact],
            }

        sources._get_json = paged
        try:
            items = sources.cleveland("tea service", 1, exact_phrase=True,
                                      smart_parts=False)
        finally:
            sources._get_json = original
        self.assertEqual([item["source_id"] for item in items], ["1001"])
        self.assertEqual(len(urls), 2)
        self.assertIn("skip=1000", urls[1])

    def test_cleveland_unquoted_query_keeps_broad_single_page_behavior(self):
        urls = []
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: (
            urls.append(url) or {"data": []}
        )
        try:
            self.assertEqual(sources.cleveland("tea service", 4), [])
        finally:
            sources._get_json = original
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
        self.assertEqual(params["limit"], ["8"])
        self.assertNotIn("smart_parts", params)

    def test_vam_fetches_object_summary_description(self):
        search_response = {"records": [{
            "systemNumber": "O1", "_primaryTitle": "Relief",
            "objectType": "Sculpture",
            "_images": {"_iiif_image_base_url": "https://iiif.test/O1/"},
        }]}
        detail_response = {"record": {
            "summaryDescription": "<p>A carved figure from a winged altarpiece.</p>",
        }}
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: (
            detail_response if "/v2/object/" in url else search_response
        )
        try:
            item = sources.vam("relief", 1)[0]
        finally:
            sources._get_json = original
        self.assertEqual(
            item["description"], "A carved figure from a winged altarpiece."
        )

    def test_vam_retains_query_bearing_object_history_for_app_evidence(self):
        search_response = {"records": [{
            "systemNumber": "O2", "_primaryTitle": "Casket",
            "objectType": "Metalwork",
            "_images": {"_iiif_image_base_url": "https://iiif.test/O2/"},
        }]}
        detail_response = {"record": {
            "summaryDescription": "A decorated silver casket.",
            "objectHistory": "Its lock has a mask of Mercury and caduceus.",
        }}
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: (
            detail_response if "/v2/object/" in url else search_response
        )
        try:
            item = sources.vam("caduceus", 1)[0]
        finally:
            sources._get_json = original
        normalized = server.normalize_result(item, query="caduceus")
        self.assertIn("caduceus", normalized["match_context"])
        self.assertIn("Object History", normalized["matched_fields"])

    def test_rijksmuseum_extracts_english_display_description(self):
        record = {"subject_of": [{
            "type": "LinguisticObject",
            "language": [{"id": "http://vocab.getty.edu/aat/300388277"}],
            "part": [{
                "type": "LinguisticObject",
                "content": "A procession crosses the town square.",
                "classified_as": [{
                    "id": "http://vocab.getty.edu/aat/300048722",
                }],
            }],
        }]}
        self.assertEqual(
            sources._rijks_description(record),
            "A procession crosses the town square.",
        )

    def test_rijksmuseum_prefers_query_matching_description_with_inherited_language(self):
        record = {"subject_of": [{
            "type": "LinguisticObject",
            "language": [{"id": "http://vocab.getty.edu/aat/300388256"}],
            "part": [{
                "type": "LinguisticObject",
                "content": (
                    "Rechtsonder: Penning met de Egyptische god Anubis en "
                    "vier andere munten."
                ),
            }],
        }, {
            "type": "LinguisticObject",
            "language": [{"id": "http://vocab.getty.edu/aat/300388277"}],
            "part": [{
                "type": "LinguisticObject",
                "content": "A generic publication-history note.",
                "classified_as": [{
                    "id": "http://vocab.getty.edu/aat/300048722",
                }],
            }],
        }]}
        self.assertIn(
            "Anubis", sources._rijks_query_description(record, "Anubis"),
        )

    def test_rijksmuseum_phrase_match_is_contiguous_and_field_local(self):
        record = {
            "identified_by": [{
                "type": "Name",
                "content": "A copy of The Night-Watch",
            }],
            "referred_to_by": [{
                "type": "LinguisticObject",
                "content": "The guards keep watch throughout the night.",
            }],
        }
        self.assertTrue(sources._rijks_phrase_matches(record, '"night watch"'))
        self.assertFalse(sources._rijks_phrase_matches(record, "watch night"))
        self.assertFalse(sources._rijks_phrase_matches({
            "identified_by": [{"type": "Name", "content": "Night"}],
            "referred_to_by": [{
                "type": "LinguisticObject", "content": "Watch",
            }],
        }, "night watch"))

    def test_rijksmuseum_unions_fields_deduplicates_and_filters_phrase(self):
        search_calls = []

        def object_record(title, description, number, visual_id):
            return {
                "identified_by": [
                    {"type": "Name", "content": title},
                    {"type": "Identifier", "content": number},
                ],
                "referred_to_by": [{
                    "type": "LinguisticObject",
                    "content": description,
                    "classified_as": [{
                        "id": "http://vocab.getty.edu/aat/300435416",
                    }],
                }],
                "shows": [{"id": visual_id}],
            }

        responses = {
            "obj-false": object_record(
                "Watch at Night", "A guard observes the city.",
                "FALSE-1", "visual-false",
            ),
            "obj-description": object_record(
                "City Guard", "A night-watch patrol crosses the square.",
                "DESC-1", "visual-description",
            ),
            "obj-title": object_record(
                "Copy of the Night Watch", "A painted copy.",
                "TITLE-1", "visual-title",
            ),
        }
        for suffix in ("false", "description", "title"):
            responses[f"visual-{suffix}"] = {
                "digitally_shown_by": [{"id": f"digital-{suffix}"}],
                "subject_to": [],
            }
            responses[f"digital-{suffix}"] = {
                "access_point": [{
                    "id": f"https://iiif.test/{suffix}/full/max/0/default.jpg",
                }],
            }

        original = sources._get_json

        def fake_get(url, *args, **kwargs):
            if "/search/collection?" in url:
                search_calls.append(url)
                if "description=" in url:
                    ids = ("obj-false", "obj-description")
                else:
                    ids = ("obj-title", "obj-description")
                return {"orderedItems": [
                    {"id": f"https://id.rijksmuseum.nl/{item}"}
                    for item in ids
                ]}
            return responses[url.rsplit("/", 1)[-1]]

        sources._get_json = fake_get
        try:
            items = sources.rijksmuseum("night watch", 2)
        finally:
            sources._get_json = original

        self.assertEqual([item["source_id"] for item in items], [
            "TITLE-1", "DESC-1",
        ])
        self.assertEqual(len(search_calls), 2)
        self.assertTrue(any("description=" in url for url in search_calls))
        self.assertTrue(any("title=" in url for url in search_calls))

    def test_rijksmuseum_search_ids_follows_page_tokens(self):
        first_ids = [f"https://id.rijksmuseum.nl/{index}" for index in range(100)]
        second_ids = [
            "https://id.rijksmuseum.nl/99",  # duplicate across pages
            "https://id.rijksmuseum.nl/100",
            "https://id.rijksmuseum.nl/101",
        ]
        calls = []
        original = sources._get_json

        def fake_get(url, *args, **kwargs):
            calls.append(url)
            if "pageToken=" in url:
                return {"orderedItems": [{"id": item} for item in second_ids]}
            return {
                "orderedItems": [{"id": item} for item in first_ids],
                "next": {"id": (
                    "https://data.rijksmuseum.nl/search/collection?"
                    "description=guard&pageToken=next"
                )},
            }

        sources._get_json = fake_get
        try:
            ids = sources._rijks_search_ids("description", "guard", 102)
        finally:
            sources._get_json = original

        self.assertEqual(len(ids), 102)
        self.assertEqual(ids[-2:], second_ids[-2:])
        self.assertEqual(len(calls), 2)

    def test_wellcome_enriches_images_with_work_artist_and_date(self):
        image_response = {"results": [{
            "id": "image-1",
            "source": {"id": "work-1", "title": "An etching"},
            "thumbnail": {
                "url": "https://iiif.wellcome.test/image/info.json",
                "license": {"label": "Public Domain Mark"},
            },
        }]}
        work_response = {
            "id": "work-1",
            "description": "A physician demonstrates the instrument to a patient.",
            "contributors": [{
                "primary": True,
                "agent": {"label": "Primary Artist"},
            }, {
                "primary": False,
                "agent": {"label": "Printmaker"},
            }],
            "production": [{"dates": [{"label": "about 1781"}]}],
            "physicalDescription": "1 etching",
        }
        calls = []
        original = sources._get_json
        def fake_get(url, *args, **kwargs):
            calls.append(url)
            return work_response if "/works/work-1?" in url else image_response
        sources._get_json = fake_get
        try:
            item = sources.wellcome("etching", 1)[0]
        finally:
            sources._get_json = original
        self.assertTrue(any("/works/work-1?" in url for url in calls))
        self.assertEqual(item["artist"], "Primary Artist; Printmaker")
        self.assertEqual(item["date"], "about 1781")
        self.assertEqual(item["medium"], "1 etching")
        self.assertEqual(
            item["description"],
            "A physician demonstrates the instrument to a patient.",
        )
        self.assertEqual(item["license"], "Public Domain Mark")

    def test_smk_runes_filter_keeps_rune_forms_and_rejects_ocr_run(self):
        def record(source_id, title):
            return {
                "object_number": source_id,
                "titles": [{"title": title}],
                "image_native": f"https://images.test/{source_id}.jpg",
            }

        search_response = {"items": [
            record("visible", "Stående model, Rune"),
            record("ocr-run", "Uden titel."),
            record("ocr-rune", "Blank"),
        ]}
        enrichments = {
            "ocr-run": [{"type": "textdetection", "data": {"tags_en": ["RUN"]}}],
            "ocr-rune": [{"type": "textdetection", "data": {"tags_en": ["RUNE"]}}],
        }
        original = sources._get_json

        def fake_get(url, *args, **kwargs):
            if "/art/search/" in url:
                return search_response
            source_id = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
            return enrichments.get(source_id, [])

        sources._get_json = fake_get
        try:
            items = sources.smk("runes", 3)
        finally:
            sources._get_json = original

        self.assertEqual(
            [item["source_id"] for item in items], ["visible", "ocr-rune"],
        )

    def test_wellcome_displays_work_primary_instead_of_matched_reverse(self):
        image_response = {"results": [{
            "id": "reverse-image",
            "source": {"id": "postcard-work", "title": "Mercury postcard"},
            "thumbnail": {
                "url": "https://iiif.wellcomecollection.org/image/CARD_0002.JP2/info.json",
                "license": {"label": "In copyright"},
            },
        }]}
        work_response = {
            "id": "postcard-work",
            "workType": {"id": "k", "type": "Format", "label": "Pictures"},
            "thumbnail": {
                "url": "https://iiif.wellcomecollection.org/thumbs/CARD_0001.JP2/full/200,/0/default.jpg",
            },
        }
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: (
            work_response if "/works/postcard-work?" in url else image_response
        )
        try:
            item = sources.wellcome("Mercury", 1)[0]
        finally:
            sources._get_json = original
        self.assertEqual(
            item["image_url"],
            "https://iiif.wellcomecollection.org/image/CARD_0001.JP2/full/max/0/default.jpg",
        )
        self.assertIn("CARD_0002.JP2", item["matched_image_url"])
        self.assertEqual(item["work_id"], "postcard-work")
        self.assertTrue(item["is_primary_view"])
        self.assertEqual(
            item["page_url"],
            "https://wellcomecollection.org/works/postcard-work",
        )

    def test_wellcome_book_keeps_matched_plate_instead_of_title_page(self):
        image_response = {"results": [{
            "id": "plate-image",
            "source": {
                "id": "book-work",
                "title": "Graphic illustrations engraved on stone",
            },
            "thumbnail": {
                "url": "https://iiif.wellcomecollection.org/image/PLATE_001.JP2/info.json",
            },
        }]}
        work_response = {
            "id": "book-work",
            "workType": {"id": "a", "type": "Format", "label": "Books"},
            "thumbnail": {
                "url": "https://iiif.wellcomecollection.org/thumbs/TITLE_001.JP2/full/200,/0/default.jpg",
            },
        }
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: (
            work_response if "/works/book-work?" in url else image_response
        )
        try:
            item = sources.wellcome("stone", 1)[0]
        finally:
            sources._get_json = original

        self.assertEqual(
            item["image_url"],
            "https://iiif.wellcomecollection.org/image/PLATE_001.JP2/full/max/0/default.jpg",
        )
        self.assertFalse(item["is_primary_view"])

    def test_wellcome_unknown_work_type_fails_closed_to_matched_image(self):
        self.assertFalse(sources._wellcome_can_use_work_primary({}))
        self.assertFalse(sources._wellcome_can_use_work_primary({
            "workType": {"label": "Ephemera"},
        }))
        self.assertTrue(sources._wellcome_can_use_work_primary({
            "workType": {"label": "Pictures"},
        }))

    def test_wellcome_exact_search_paginates_the_candidate_budget(self):
        calls = []
        original = sources._get_json

        def image(index):
            return {
                "id": f"image-{index}",
                "source": {"id": f"work-{index}", "title": f"Work {index}"},
                "thumbnail": {
                    "url": f"https://iiif.wellcome.test/{index}/info.json",
                },
            }

        def fake_get(url, *args, **kwargs):
            calls.append(url)
            if "/works/" in url:
                return {}
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            page = int(params.get("page", ["1"])[0])
            page_size = int(params["pageSize"][0])
            start = (page - 1) * page_size
            return {
                "results": [image(index) for index in range(start, start + page_size)],
                "nextPage": "next" if page == 1 else "",
            }

        sources._get_json = fake_get
        try:
            items = sources.wellcome(
                "guardian threshold", 150,
                exact_phrases=("guardian threshold",),
            )
        finally:
            sources._get_json = original

        image_calls = [url for url in calls if "/images?" in url]
        self.assertEqual(len(items), 150)
        self.assertEqual(len(image_calls), 2)
        first = urllib.parse.parse_qs(urllib.parse.urlsplit(image_calls[0]).query)
        second = urllib.parse.parse_qs(urllib.parse.urlsplit(image_calls[1]).query)
        self.assertEqual(first["pageSize"], ["100"])
        self.assertEqual(second["pageSize"], ["100"])
        self.assertEqual(second["page"], ["2"])
        self.assertIn("source.subjects", first["include"][0])

    def test_wellcome_exposes_full_text_metadata_for_exact_verification(self):
        work = {
            "title": "A vessel",
            "subjects": [{"label": "Guardian—of the threshold"}],
            "notes": [{"contents": "Ceremonial object"}],
        }
        values = sources._wellcome_search_text(work, {})
        self.assertIn("Guardian—of the threshold", values)
        self.assertIn("Ceremonial object", values)
        self.assertNotIn("Work", values)

    def test_wellcome_rejects_single_term_hit_from_broader_provider_stem(self):
        image_response = {"results": [{
            "id": "run-image",
            "source": {"id": "run-work", "title": "Run no risks"},
            "thumbnail": {"url": "https://iiif.wellcome.test/run/info.json"},
        }, {
            "id": "rune-image",
            "source": {"id": "rune-work", "title": "A Viking rune stone"},
            "thumbnail": {"url": "https://iiif.wellcome.test/rune/info.json"},
        }]}
        works = {
            "run-work": {"description": "A road-safety milk bottle cap."},
            "rune-work": {"description": "A stone inscribed with a rune."},
        }
        original = sources._get_json

        def fake_get(url, *args, **kwargs):
            for work_id, work in works.items():
                if f"/works/{work_id}?" in url:
                    return work
            return image_response

        sources._get_json = fake_get
        try:
            items = sources.wellcome("runes", 1)
        finally:
            sources._get_json = original

        self.assertEqual([item["source_id"] for item in items], ["rune-image"])

    def test_wellcome_single_term_verifier_keeps_safe_noun_inflections(self):
        self.assertTrue(sources._wellcome_single_term_hit_is_supported(
            "runes", ["A carved rune"],
        ))
        self.assertTrue(sources._wellcome_single_term_hit_is_supported(
            "rune", ["Several runes"],
        ))
        self.assertFalse(sources._wellcome_single_term_hit_is_supported(
            "runes", ["Run no risks"],
        ))

    def test_encrypted_credentials_round_trip_from_separate_files(self):
        original_env = {
            name: keys.os.environ.get(name)
            for name in ("EUROPEANA_API_KEY", "SEARCH_KEYS_FILE",
                         "SEARCH_CREDENTIALS_DIR")
        }
        with tempfile.TemporaryDirectory() as directory:
            keys.write_encrypted({"europeana": "round-trip-key"}, Path(directory))
            keys.os.environ.pop("EUROPEANA_API_KEY", None)
            keys.os.environ.pop("SEARCH_KEYS_FILE", None)
            keys.os.environ["SEARCH_CREDENTIALS_DIR"] = directory
            try:
                self.assertEqual(keys.get_key("europeana"), "round-trip-key")
                self.assertEqual(
                    {path.name for path in Path(directory).iterdir()},
                    {"providers.enc", "runtime.key"},
                )
                self.assertNotIn(
                    "round-trip-key",
                    (Path(directory) / "providers.enc").read_text(),
                )
            finally:
                for name, value in original_env.items():
                    if value is None:
                        keys.os.environ.pop(name, None)
                    else:
                        keys.os.environ[name] = value

    def test_yale_parser_keeps_only_reusable_art_museum_images(self):
        record = {
            "id": "https://lux.collections.yale.edu/data/object/yale-1",
            "_label": "Museum work",
            "member_of": [{"_label": "Yale University Art Gallery"}],
            "subject_to": [{"_label": "Public Domain"}],
            "classified_as": [{"_label": "Painting"}],
            "produced_by": {
                "carried_out_by": [{"_label": "Example Artist"}],
                "timespan": {"_label": "1650"},
            },
            "referred_to_by": [{
                "type": "LinguisticObject",
                "content": "A richly dressed sitter stands beside a table.",
                "classified_as": [{
                    "id": "http://vocab.getty.edu/aat/300435416",
                    "_label": "Description",
                }],
                "language": [{"id": "http://vocab.getty.edu/aat/300388277"}],
            }],
            "representation": [{
                "digitally_shown_by": [{
                    "type": "DigitalObject", "format": "image/jpeg",
                    "digitally_available_via": [{
                        "type": "DigitalService",
                        "access_point": [{
                            "id": "https://images.collections.yale.edu/iiif/2/example"
                        }],
                    }],
                }],
            }],
        }
        item = additional_sources.parse_yale_record(record)
        self.assertIsNotNone(item)
        self.assertEqual(item["artist"], "Example Artist")
        self.assertEqual(item["medium"], "Painting")
        self.assertEqual(
            item["description"],
            "A richly dressed sitter stands beside a table.",
        )
        self.assertEqual(
            item["image_url"],
            "https://images.collections.yale.edu/iiif/2/example/"
            "full/max/0/default.jpg",
        )
        restricted = dict(record)
        restricted["subject_to"] = [{"_label": "All rights reserved"}]
        self.assertIsNone(additional_sources.parse_yale_record(restricted))

    def test_mia_parser_requires_public_domain_public_image(self):
        payload = {"hits": {"hits": [{"_id": "529", "_source": {
            "id": 529, "title": "Lucretia", "artist": "Rembrandt",
            "dated": "1666", "medium": "Oil on canvas",
            "text": "A detailed curatorial description of the painting.",
            "rights_type": "Public Domain", "image": "valid",
            "public_access": 1, "image_width": 4812, "image_height": 5787,
            "Cache_Location": "000000\\500\\20\\529",
            "Primary_RenditionNumber": "mia_12345.jpg",
        }}, {"_id": "530", "_source": {
            "id": 530, "title": "Copyrighted", "rights_type": "In Copyright",
            "image": "valid", "public_access": 1,
        }}]}}
        items = additional_sources.parse_mia_response(payload, 10)
        self.assertEqual([item["source_id"] for item in items], ["529"])
        self.assertEqual((items[0]["width"], items[0]["height"]), (4812, 5787))
        self.assertEqual(
            items[0]["description"],
            "A detailed curatorial description of the painting.",
        )
        self.assertEqual(
            items[0]["thumb_url"],
            "https://img.artsmia.org/web_objects_cache/"
            "000000/500/20/529/mia_12345_800.jpg",
        )
        self.assertEqual(
            items[0]["image_url"],
            "https://img.artsmia.org/web_objects_cache/"
            "000000/500/20/529/mia_12345_full.jpg",
        )
        self.assertTrue(items[0]["requires_source_visit"])

    def test_paris_parser_uses_only_public_api_visuals_and_original_file(self):
        payload = {"data": {"nodeQuery": {"entities": [{
            "entityId": 226737, "title": "Sunset on the Seine",
            "absolutePath": "http://parismuseescollections.paris.fr/node/226737",
            "fieldOeuvreAuteurs": [{"entity": {"name": "Claude Monet"}}],
            "fieldDateProduction": {"sort": "1880"},
            "fieldMateriauxTechnique": [{"entity": {"name": "Oil paint"}}],
            "fieldVisuelsPrincipals": [{"entity": {"vignette":
                "http://apicollections.parismusees.paris.fr/sites/default/"
                "files/styles/thumbnail/public/2020-01/monet.webp?itok=abc"
            }}],
        }]}}}
        item = additional_sources.parse_paris_musees_response(payload, 10)[0]
        self.assertEqual(item["artist"], "Claude Monet")
        self.assertEqual(item["license"], "CC0")
        self.assertEqual(
            item["image_url"],
            "https://apicollections.parismusees.paris.fr/sites/default/"
            "files/2020-01/monet.webp",
        )

    def test_paris_detail_parser_combines_iconographic_and_historical_text(self):
        document = '''
        <div class="field field-name-field-oeuvre-description-icono">
          <div class="field-label">Description iconographique:</div>
          <div class="field-item"><p>A sunset reflects across the Seine.</p></div>
        </div>
        <div class="field field-name-field-commentaire-historique">
          <div class="field-label">Commentaire historique:</div>
          <div class="field-item"><p>Monet painted the thaw near Lavacourt.</p></div>
        </div>
        '''
        self.assertEqual(
            additional_sources.parse_paris_musees_detail_page(document),
            "A sunset reflects across the Seine.\n\n"
            "Monet painted the thaw near Lavacourt.",
        )

    def test_museum_commons_requires_structured_filters_and_reusable_license(self):
        payload = {"query": {"pages": {
            "1": {"title": "File:Museum work.jpg", "imageinfo": [{
                "mime": "image/webp", "url": "https://commons.test/full.webp",
                "thumburl": "https://commons.test/thumb.webp",
                "descriptionurl": "https://commons.test/wiki/Museum_work",
                "width": 3000, "height": 2000,
                "extmetadata": {
                    "Artist": {"value": "<b>Example Artist</b>"},
                    "DateTimeOriginal": {
                        "value": "circa 1509date QS:P571,+1509-00-00T00:00:00Z/9"
                    },
                    "ImageDescription": {"value": "Saint Peter in prison"},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                },
            }]},
            "2": {"title": "File:Non-reusable.jpg", "imageinfo": [{
                "mime": "image/jpeg", "url": "https://commons.test/no.jpg",
                "width": 3000, "height": 2000,
                "extmetadata": {"LicenseShortName": {"value": "Copyrighted"}},
            }]},
        }}}
        calls = []
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: calls.append(url) or payload
        try:
            items = additional_sources.museum_commons("saint peter", 10)
        finally:
            sources._get_json = original
        query = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)["gsrsearch"][0]
        self.assertIn("haswbstatement:P195", query)
        self.assertIn("haswbstatement:P180", query)
        self.assertEqual([item["source_id"] for item in items], ["File:Museum work.jpg"])
        self.assertEqual(items[0]["artist"], "Example Artist")
        self.assertEqual(items[0]["date"], "circa 1509")

    def test_commons_metadata_filter_is_typo_tolerant_but_rejects_weak_matches(self):
        self.assertTrue(additional_sources.commons_metadata_is_relevant(
            "peter escapes from prisoin",
            "The Liberation of Saint Peter. An angel visits Peter in prison.",
        ))
        self.assertFalse(additional_sources.commons_metadata_is_relevant(
            "peter escapes from prisoin",
            "Portrait of Saint Peter holding keys",
        ))
        self.assertTrue(additional_sources.commons_metadata_is_relevant(
            "kundalini", "TaTvA Kundalini performing live",
        ))
        self.assertFalse(additional_sources.commons_metadata_is_relevant(
            "kundalini", "Camp Chesterfield general account ledger",
        ))
        self.assertTrue(additional_sources.commons_metadata_is_relevant(
            "Caduceus of Mercury",
            '"Cato" on constitutional money in the Charleston mercury',
        ))

    def test_commons_parser_rejects_dpla_pages_matching_only_mercury(self):
        title = (
            'File:"Cato" on constitutional "money" and legal tender. In 12 '
            'no. from the Charleston mercury - DPLA - '
            '20b4f8f4b36bd2c33baf94189f183c71 (page 24).jpg'
        )
        payload = {"query": {"pages": {"24": {
            "title": title,
            "imageinfo": [{
                "mime": "image/jpeg",
                "url": "https://commons.test/cato-page-24.jpg",
                "extmetadata": {
                    "ObjectName": {"value": (
                        '"Cato" on constitutional "money" and legal tender. '
                        "In 12 no. from the Charleston mercury"
                    )},
                    "ImageDescription": {"value": (
                        "Issued in a case; Charleston mercury, Evans & "
                        "Cogswell, 1862, Duke University Libraries"
                    )},
                    "LicenseShortName": {"value": "Public domain"},
                },
            }],
        }}}}
        self.assertEqual(
            additional_sources.parse_museum_commons_response(
                payload, 10, "Caduceus of Mercury"
            ),
            [],
        )

    def test_commons_rejects_book_page_even_when_parent_title_fully_matches(self):
        title = (
            "File:The caduceus of Mercury - DPLA - "
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (page 7).jpg"
        )
        payload = {"query": {"pages": {"7": {
            "title": title,
            "imageinfo": [{
                "mime": "image/jpeg", "url": "https://commons.test/page-7.jpg",
                "extmetadata": {
                    "ObjectName": {"value": "The caduceus of Mercury"},
                    "Description": {"value": (
                        "The caduceus of Mercury, donated by Example Library"
                    )},
                    "LicenseShortName": {"value": "Public domain"},
                },
            }],
        }}}}
        self.assertEqual(
            additional_sources.parse_museum_commons_response(
                payload, 10, "Caduceus of Mercury"
            ),
            [],
        )

    def test_commons_keeps_numbered_page_with_page_level_illustration_metadata(self):
        title = (
            "File:The caduceus of Mercury - DPLA - "
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (page 8).jpg"
        )
        payload = {"query": {"pages": {"8": {
            "title": title,
            "imageinfo": [{
                "mime": "image/jpeg", "url": "https://commons.test/page-8.jpg",
                "extmetadata": {
                    "ObjectName": {"value": "The caduceus of Mercury"},
                    "ImageDescription": {"value": (
                        "The caduceus of Mercury. Plate 8: an engraving depicting "
                        "Mercury holding the caduceus."
                    )},
                    "LicenseShortName": {"value": "Public domain"},
                },
            }],
        }}}}
        items = additional_sources.parse_museum_commons_response(
            payload, 10, "Caduceus of Mercury"
        )
        self.assertEqual([item["source_id"] for item in items], [title])

    def test_commons_long_description_surfaces_query_match_context(self):
        description = (
            "A historical account of Norwegian volunteers and their uniforms. "
            + "Background material. " * 80
            + "SS runes on a black diamond were worn on the right upper arm. "
            + "A small badge also displayed silver SS runes."
        )
        excerpt = additional_sources._query_centered_excerpt(
            description, "runes", 300
        )
        self.assertIn("SS runes on a black diamond", excerpt)
        self.assertIn("silver SS runes", excerpt)
        self.assertLessEqual(len(excerpt), 301)

    def test_commons_parser_filters_irrelevant_metadata_before_limit(self):
        payload = {"query": {"pages": {
            "1": {"title": "File:Generic painting.jpg", "imageinfo": [{
                "mime": "image/jpeg", "url": "https://commons.test/generic.jpg",
                "extmetadata": {
                    "ImageDescription": {"value": "A generic landscape"},
                    "LicenseShortName": {"value": "Public domain"},
                },
            }]},
            "2": {"title": "File:Kundalini symbol.jpg", "imageinfo": [{
                "mime": "image/jpeg", "url": "https://commons.test/kundalini.jpg",
                "extmetadata": {
                    "ImageDescription": {"value": "Diagram of Kundalini energy"},
                    "LicenseShortName": {"value": "Public domain"},
                },
            }]},
        }}}
        items = additional_sources.parse_museum_commons_response(
            payload, 10, "kundalini"
        )
        self.assertEqual(
            [item["source_id"] for item in items], ["File:Kundalini symbol.jpg"]
        )

    def test_commons_parser_rejects_match_found_only_in_grouping_category(self):
        payload = {"query": {"pages": {"1": {
            "title": "File:Chain with Birds and Geometric Motifs MET DT12030.jpg",
            "imageinfo": [{
                "mime": "image/jpeg", "url": "https://commons.test/reverse.jpg",
                "extmetadata": {
                    "ObjectName": {"value": "Chain with Birds and Geometric Motifs"},
                    "ImageDescription": {"value": "Kievan Rus; dress ornament"},
                    "Categories": {"value": "Chains with Birds and Trees of Life, MET 17.190.705-6"},
                    "LicenseShortName": {"value": "CC0"},
                },
            }],
        }}}}
        self.assertEqual(
            additional_sources.parse_museum_commons_response(
                payload, 10, "Kabbalistic Tree of Life"
            ),
            [],
        )

    def test_commons_parser_prefers_earlier_met_primary_view(self):
        def page(asset, url):
            return {
                "title": f"File:Geometric Motifs MET DT{asset}.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg", "url": url,
                    "extmetadata": {
                        "ObjectName": {"value": "Geometric Motifs"},
                        "Categories": {"value": "Geometric Motifs, MET 17.190.706"},
                        "LicenseShortName": {"value": "CC0"},
                    },
                }],
            }
        payload = {"query": {"pages": {
            "reverse": page(12030, "https://commons.test/reverse.jpg"),
            "front": page(12029, "https://commons.test/front.jpg"),
        }}}
        items = additional_sources.parse_museum_commons_response(
            payload, 10, "geometric motifs"
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["image_url"], "https://commons.test/front.jpg")

    def test_universal_comasonry_parses_gallery_index_and_images(self):
        index = """
        <a href="/en/gallery/secret-teachings">
          <h6>The Secret Teachings Of All Ages</h6>
          <img src="/Assets/Images/Gallery_Background_Images/cover.jpg">
        </a>
        <a href="/en/article/not-a-gallery"><h6>Ignore me</h6></a>
        """
        self.assertEqual(
            additional_sources.parse_universal_comasonry_gallery_index(index),
            [("/en/gallery/secret-teachings", "The Secret Teachings Of All Ages")],
        )
        gallery = r"""
        <a href="/en/gallery/secret-teachings/solomon-shedd">
          <p><img src="\Assets\Images\Gallery_Images\image-15.jpg"
             alt="PLATE 15: Solomon &amp; the Shedds"></p>
        </a>
        <img src="/assets/images/logo.png" alt="Ignore me">
        """
        items = additional_sources.parse_universal_comasonry_gallery_page(
            gallery, "The Secret Teachings Of All Ages"
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "universal_comasonry")
        self.assertEqual(items[0]["title"], "PLATE 15: Solomon & the Shedds")
        self.assertEqual(items[0]["source_id"], "image-15")
        self.assertEqual(
            items[0]["image_url"],
            "https://www.universalfreemasonry.org/Assets/Images/"
            "Gallery_Images/image-15.jpg",
        )
        self.assertTrue(items[0]["requires_source_visit"])

    def test_universal_comasonry_parses_image_detail_description(self):
        detail = """
        <nav><span>Adam in an unrelated menu</span></nav>
        <span id="GalleryImageDescription"><p>
          Such is <em>Adam Kadman</em>, the primordial Adam of the Kabalists.
        </p></span>
        <footer><p>Generic Masonic gallery text</p></footer>
        """
        self.assertEqual(
            additional_sources.parse_universal_comasonry_detail_page(detail),
            "Such is Adam Kadman, the primordial Adam of the Kabalists.",
        )

    def test_universal_comasonry_catalog_enriches_images_from_detail_pages(self):
        documents = {
            additional_sources.UNIVERSAL_COMASONRY_GALLERIES_URL: """
                <a href="/en/gallery/secret-teachings">
                  <h6>The Secret Teachings Of All Ages</h6>
                </a>
            """,
            "https://www.universalfreemasonry.org/en/gallery/secret-teachings": """
                <a href="/en/gallery/secret-teachings/grand-man">
                  <img src="/Assets/Images/Gallery_Images/grand-man.jpg"
                       alt="PLATE 25: Grand Man of The Zohar">
                </a>
            """,
            "https://www.universalfreemasonry.org/en/gallery/secret-teachings/grand-man": """
                <span id="GalleryImageDescription">
                  Such is <em>Adam Kadman</em>, the primordial Adam.
                </span>
            """,
        }
        original = additional_sources._universal_comasonry_fetch_text
        additional_sources._universal_comasonry_fetch_text = documents.__getitem__
        try:
            items = additional_sources._build_universal_comasonry_catalog()
        finally:
            additional_sources._universal_comasonry_fetch_text = original
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["search_text"],
                         "Such is Adam Kadman, the primordial Adam.")
        self.assertEqual(items[0]["description"], items[0]["search_text"])

    def test_universal_comasonry_reads_legacy_catalog_without_detail_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "universal_comasonry_catalog.json"
            path.write_text('[{"title":"Legacy record"}]', encoding="utf-8")
            self.assertEqual(
                additional_sources._read_universal_comasonry_catalog(path),
                [{"title": "Legacy record"}],
            )

    def test_universal_comasonry_returns_legacy_catalog_before_refreshing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "universal_comasonry_catalog.json"
            legacy = [{"title": "Legacy record", "medium": "Gallery"}]
            path.write_text(
                '[{"title":"Legacy record","medium":"Gallery"}]',
                encoding="utf-8",
            )
            scheduled = []
            original_path = additional_sources._universal_comasonry_catalog_path
            original_schedule = additional_sources._schedule_universal_comasonry_refresh
            original_build = additional_sources._build_universal_comasonry_catalog
            additional_sources._universal_comasonry_catalog_path = lambda: path
            additional_sources._schedule_universal_comasonry_refresh = (
                lambda target, records, *, rebuild_index: scheduled.append(
                    (target, records, rebuild_index)
                )
            )
            additional_sources._build_universal_comasonry_catalog = (
                lambda **_kwargs: self.fail("legacy cache should not block on a rebuild")
            )
            try:
                records = additional_sources._load_universal_comasonry_catalog()
            finally:
                additional_sources._universal_comasonry_catalog_path = original_path
                additional_sources._schedule_universal_comasonry_refresh = original_schedule
                additional_sources._build_universal_comasonry_catalog = original_build
            self.assertEqual(records, legacy)
            self.assertEqual(scheduled, [(path, legacy, False)])

    def test_universal_comasonry_cold_cache_defers_detail_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "universal_comasonry_catalog.json"
            base = [{"title": "Indexed record", "medium": "Gallery"}]
            builds = []
            scheduled = []
            original_path = additional_sources._universal_comasonry_catalog_path
            original_schedule = additional_sources._schedule_universal_comasonry_refresh
            original_build = additional_sources._build_universal_comasonry_catalog
            additional_sources._universal_comasonry_catalog_path = lambda: path
            additional_sources._schedule_universal_comasonry_refresh = (
                lambda target, records, *, rebuild_index: scheduled.append(
                    (target, records, rebuild_index)
                )
            )
            additional_sources._build_universal_comasonry_catalog = (
                lambda *, enrich=True: builds.append(enrich) or base
            )
            try:
                records = additional_sources._load_universal_comasonry_catalog()
            finally:
                additional_sources._universal_comasonry_catalog_path = original_path
                additional_sources._schedule_universal_comasonry_refresh = original_schedule
                additional_sources._build_universal_comasonry_catalog = original_build
            self.assertEqual(records, base)
            self.assertEqual(builds, [False])
            self.assertEqual(scheduled, [(path, base, False)])
            self.assertEqual(
                additional_sources._read_universal_comasonry_catalog(path), base
            )

    def test_universal_comasonry_search_uses_cached_catalog(self):
        original = additional_sources._load_universal_comasonry_catalog
        additional_sources._load_universal_comasonry_catalog = lambda: [{
            "title": "PLATE 15: Solomon and the Shedds",
            "medium": "The Secret Teachings Of All Ages",
            "page_url": "https://www.universalfreemasonry.org/en/gallery/"
                        "secret-teachings/solomon-shedd",
            "search_text": "",
        }, {
            "title": "The Zodiacal Egg", "medium": "Another gallery",
            "page_url": "https://www.universalfreemasonry.org/en/gallery/other/egg",
            "search_text": "",
        }]
        try:
            items = additional_sources.universal_comasonry("king solomon", 10)
        finally:
            additional_sources._load_universal_comasonry_catalog = original
        self.assertEqual([item["title"] for item in items], [
            "PLATE 15: Solomon and the Shedds"
        ])

    def test_universal_comasonry_searches_detail_description(self):
        original = additional_sources._load_universal_comasonry_catalog
        additional_sources._load_universal_comasonry_catalog = lambda: [{
            "title": "PLATE 25: Grand Man of The Zohar",
            "medium": "The Secret Teachings Of All Ages",
            "page_url": "https://www.universalfreemasonry.org/en/gallery/"
                        "secret-teachings/grand-man",
            "search_text": "Such is Adam Kadman, the primordial Adam of the Kabalists.",
            "description": "Such is Adam Kadman, the primordial Adam of the Kabalists.",
        }]
        try:
            items = additional_sources.universal_comasonry("adam kadman", 10)
        finally:
            additional_sources._load_universal_comasonry_catalog = original
        self.assertEqual(
            [item["title"] for item in items],
            ["PLATE 25: Grand Man of The Zohar"],
        )

    def test_universal_comasonry_does_not_match_tree_inside_streets(self):
        irrelevant = {
            "title": "Freemason's Hall, Queen Streets, 1775",
            "medium": "The Art & Architecture of Freemasonry",
            "page_url": "https://example.test/freemason-hall-1775",
            "search_text": "An artistic depiction of Freemason's Hall, London.",
        }
        relevant = {
            "title": "The Kabbalistic Tree of Life",
            "medium": "Diagram",
            "page_url": "https://example.test/tree-of-life",
            "search_text": "The ten Sephiroth are arranged on the Tree of Life.",
        }
        self.assertEqual(
            additional_sources._universal_comasonry_match_score(
                "Kabbalistic Tree of Life", irrelevant
            )[0],
            0.0,
        )
        self.assertGreater(
            additional_sources._universal_comasonry_match_score(
                "Kabbalistic Tree of Life", relevant
            )[0],
            0.0,
        )

    def test_getty_parser_accepts_only_cc0_and_builds_full_iiif_url(self):
        payload = {"data": [{
            "id": "object/c88b3df0-de91-4f5b-a9ef-7b2b9a6d8abb",
            "primary_name": "Irises", "date_created": "1889",
            "culture": ["Dutch"], "slug_with_path": "/object/103JNH",
            "producers": [{"primary_name": "Vincent van Gogh"}],
            "manifest": {
                "license": "http://creativecommons.org/publicdomain/zero/1.0/",
                "thumbUuid": "8c255d80-7382-46db-9fa8-892c0d37247e",
                "thumb": "https://media.getty.edu/iiif/image/example/thumb.jpg",
            },
        }, {
            "id": "object/restricted", "primary_name": "Restricted work",
            "manifest": {
                "license": "http://rightsstatements.org/vocab/InC/1.0/",
                "thumbUuid": "restricted-image",
            },
        }]}
        items = additional_sources.parse_getty_response(payload, 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["artist"], "Vincent van Gogh")
        self.assertEqual(items[0]["license"], "CC0")
        self.assertEqual(
            items[0]["image_url"],
            "https://media.getty.edu/iiif/image/"
            "8c255d80-7382-46db-9fa8-892c0d37247e/full/max/0/default.jpg",
        )
        self.assertEqual(
            items[0]["thumb_url"],
            "https://media.getty.edu/iiif/image/"
            "8c255d80-7382-46db-9fa8-892c0d37247e/"
            "full/!1200,1200/0/default.jpg",
        )
        self.assertEqual(
            items[0]["page_url"],
            "https://www.getty.edu/art/collection/object/103JNH",
        )

    def test_getty_detail_parser_reads_structured_artwork_description(self):
        document = '''
        <meta name="description" content="Generic collection page">
        <script type="application/ld+json">
          {"name":"Irises","description":"Van Gogh painted the irises from nature in the asylum garden."}
        </script>
        '''
        self.assertEqual(
            additional_sources.parse_getty_detail_page(document),
            "Van Gogh painted the irises from nature in the asylum garden.",
        )

    def test_getty_linked_art_context_exposes_query_bearing_provenance(self):
        record = {"referred_to_by": [{
            "type": "LinguisticObject",
            "_label": (
                "Provenance acquisition associated with Atlantis Antiquities, "
                "Ltd.; subsequently sold to the museum in 1987."
            ),
        }]}
        context = additional_sources._getty_linked_art_query_context(
            record, "Atlantis"
        )
        self.assertIn("Atlantis Antiquities", context)

    def test_getty_manifest_requires_query_terms_on_one_child_page(self):
        def canvas(identifier):
            return {
                "id": f"https://media.getty.edu/iiif/manifest/canvas/{identifier}",
                "type": "Canvas",
                "items": [{"items": [{"body": {
                    "id": f"https://media.getty.edu/iiif/image/{identifier}/full/max/0/default.jpg",
                    "type": "Image",
                }}]}],
            }

        manifest = {
            "items": [canvas("sun"), canvas("stone"), canvas("match")],
            "structures": [{"items": [
                {"id": "https://media.getty.edu/iiif/manifest/canvas/sun",
                 "type": "Canvas", "label": {"en": ["The Sun-lodge"]}},
                {"id": "https://media.getty.edu/iiif/manifest/canvas/stone",
                 "type": "Canvas", "label": {"en": ["Buffalo-stones"]}},
                {"id": "https://media.getty.edu/iiif/manifest/canvas/match",
                 "type": "Canvas", "label": {"en": ["Aztec Sun Stone"]}},
            ]}],
        }
        page = additional_sources._getty_manifest_matching_page(
            manifest, "Aztec Sun Stone"
        )
        self.assertEqual(page["title"], "Aztec Sun Stone")
        self.assertIn("/image/match/", page["image_url"])
        manifest["structures"][0]["items"].pop()
        self.assertIsNone(additional_sources._getty_manifest_matching_page(
            manifest, "Aztec Sun Stone"
        ))

    def test_loc_parser_uses_largest_derivative_and_rights_statement(self):
        payload = {"results": [{
            "digitized": True, "access_restricted": False,
            "id": "http://www.loc.gov/item/123/", "title": "Historic view",
            "description": ["A crowd gathers outside the station."],
            "url": "http://www.loc.gov/item/123/",
            "image_url": [
                "https://tile.loc.gov/example-small.jpg#h=300&w=400",
                "https://tile.loc.gov/example.jpg#h=1200&w=1800",
            ],
            "item": {
                "id": "123", "contributors": ["Maker"], "date": "1901",
                "medium": ["photographic print"],
                "rights_advisory": "No known restrictions on publication.",
            },
        }]}
        item = additional_sources.parse_loc_response(payload, 10)[0]
        self.assertEqual((item["width"], item["height"]), (1800, 1200))
        self.assertEqual(item["license"], "No known restrictions")
        self.assertEqual(item["description"], "A crowd gathers outside the station.")
        normalized = server.normalize_result(item)
        self.assertEqual(normalized["preview_click_action"], "visit_website")
        self.assertEqual(normalized["preview_click_url"], "https://www.loc.gov/item/123/")

    def test_loc_parser_excludes_multipage_books_matched_through_ocr(self):
        book = {
            "digitized": True, "access_restricted": False,
            "id": "http://www.loc.gov/item/08016592/", "title": "Poems,",
            "original_format": ["photo, print, drawing", "book"],
            "image_url": [
                "https://tile.loc.gov/cover.jpg#h=1200&w=800",
            ],
            "resources": [{
                "files": 348,
                "fulltext_derivative": "https://tile.loc.gov/book.text.json",
                "text_file": "https://tile.loc.gov/book.text.txt",
                "representative_index": 10,
            }],
            "item": {"id": "08016592", "format": ["text"]},
        }
        photo = {
            "digitized": True, "access_restricted": False,
            "id": "http://www.loc.gov/item/photo/", "title": "Moses and the serpent",
            "original_format": ["photo, print, drawing"],
            "image_url": [
                "https://tile.loc.gov/photo.jpg#h=800&w=1200",
            ],
            "item": {"id": "photo", "format": ["still image"]},
        }
        items = additional_sources.parse_loc_response(
            {"results": [book, photo]}, 10
        )
        self.assertEqual([item["source_id"] for item in items], ["photo"])

    def test_loc_parser_excludes_explicit_text_only_catalog_cards(self):
        catalog_card = {
            "digitized": True, "access_restricted": False,
            "id": "http://www.loc.gov/item/card/", "title": "Calipso",
            "genre": ["card catalogs"],
            "image_url": ["https://tile.loc.gov/card.jpg#h=900&w=1500"],
            "resources": [{"caption": "Card catalog image", "files": 1}],
            "item": {
                "id": "card", "genre": ["card catalogs"],
                "notes": ["This is a digitized catalog card, not an audio recording."],
            },
        }
        self.assertEqual(
            additional_sources.parse_loc_response({"results": [catalog_card]}, 10),
            [],
        )

    def test_loc_catalog_card_filter_requires_corresponding_resource_evidence(self):
        illustrated_work = {
            "digitized": True, "access_restricted": False,
            "id": "http://www.loc.gov/item/poster/", "title": "Library poster",
            "subject": ["card catalogs"],
            "image_url": ["https://tile.loc.gov/poster.jpg#h=1200&w=800"],
            "resources": [{"caption": "Illustrated poster", "files": 1}],
            "item": {"id": "poster", "format": ["still image"]},
        }
        items = additional_sources.parse_loc_response(
            {"results": [illustrated_work]}, 10
        )
        self.assertEqual([item["source_id"] for item in items], ["poster"])

    def test_harvard_parser_excludes_restricted_images_and_builds_iiif_urls(self):
        payload = {"records": [{
            "objectid": 42, "title": "Open work", "dated": "17th century",
            "description": "<p>A saint reads beside an open window.</p>",
            "classification": "Prints", "imagepermissionlevel": 0,
            "url": "http://www.harvardartmuseums.org/collections/object/42",
            "people": [{"role": "Artist", "displayname": "An Artist"}],
            "images": [{
                "displayorder": 1, "baseimageurl":
                    "https://nrs.harvard.edu/urn-3:HUAM:ABC_dynmc",
                "width": 2400, "height": 1800,
                "copyright": "President and Fellows of Harvard College",
            }],
        }, {
            "objectid": 43, "title": "Restricted work",
            "imagepermissionlevel": 1,
            "primaryimageurl": "https://nrs.harvard.edu/urn-3:HUAM:XYZ",
        }]}
        items = additional_sources.parse_harvard_response(payload, 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["artist"], "An Artist")
        self.assertEqual(items[0]["description"], "A saint reads beside an open window.")
        self.assertEqual((items[0]["width"], items[0]["height"]), (2400, 1800))
        self.assertEqual(
            items[0]["image_url"],
            "https://nrs.harvard.edu/urn-3:HUAM:ABC_dynmc/full/full/0/default.jpg",
        )
        self.assertEqual(
            items[0]["thumb_url"],
            "https://nrs.harvard.edu/urn-3:HUAM:ABC_dynmc/"
            "full/!1024,1024/0/default.jpg",
        )

    def test_harvard_description_includes_series_title_when_prose_is_absent(self):
        record = {
            "title": "Spring",
            "titles": [
                {"titletype": "Title", "title": "Spring", "displayorder": 1},
                {"titletype": "Series/Book Title",
                 "title": "Four Seasons, with the Zodiac", "displayorder": 2},
            ],
        }
        self.assertEqual(
            additional_sources._harvard_description(record),
            "Series/Book Title: Four Seasons, with the Zodiac.",
        )

    def test_description_language_maps_prefer_english(self):
        descriptions = {"nl": ["Nederlands"], "en-GB": ["English context"]}
        self.assertEqual(sources._description_text(descriptions), "English context")
        self.assertEqual(
            additional_sources._clean_description(descriptions), "English context",
        )

    def test_harvard_proxy_uses_official_signed_cdn_url(self):
        source = (
            "https://nrs.harvard.edu/urn-3:HUAM:ABC_dynmc/"
            "full/!1024,1024/0/default.jpg"
        )
        self.assertEqual(
            server.harvard_cdn_url(source),
            "https://images.harvardartmuseums.org/"
            "urn-3:HUAM:ABC_dynmc:IMAGE/full/!1024,1024/0/default.jpg",
        )
        with self.assertRaises(server.ImageProxyError):
            server.harvard_cdn_url("https://example.test/image.jpg")

    def test_harvard_proxy_accepts_lowercase_api_urn(self):
        source = (
            "https://nrs.harvard.edu/urn-3:huam:DDC112559_dynmc/"
            "full/!1024,1024/0/default.jpg"
        )
        self.assertEqual(
            server.harvard_cdn_url(source),
            "https://images.harvardartmuseums.org/"
            "urn-3:huam:DDC112559_dynmc:IMAGE/full/!1024,1024/0/default.jpg",
        )

    def test_nga_catalog_builds_and_searches_official_csv_shape(self):
        objects = (
            "objectid,title,attribution,displaydate,medium,classification\n"
            "7,The Liberation of Saint Peter,Example Artist,1642,oil on canvas,Painting\n"
            "8,Unrelated Work,Other Artist,1700,ink,Drawing\n"
        )
        images = (
            "uuid,iiifurl,iiifthumburl,viewtype,sequence,width,height,openaccess,"
            "depictstmsobjectid,assistivetext\n"
            "a,https://api.nga.gov/iiif/a,https://api.nga.gov/thumb/a,primary,0,"
            "3000,2000,1,7,Saint Peter escaping prison\n"
            "b,https://api.nga.gov/iiif/b,https://api.nga.gov/thumb/b,primary,0,"
            "1000,900,0,8,Not open access\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "nga.zip"
            database_path = Path(directory) / "nga.db"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("opendata-main/data/objects.csv", objects)
                archive.writestr("opendata-main/data/published_images.csv", images)
            catalog = additional_sources.NGACatalog(database_path)
            catalog.build_from_archive(archive_path)
            results = catalog.search("Peter prison", 10)
        self.assertEqual([item["source_id"] for item in results], ["7"])
        self.assertEqual((results[0]["width"], results[0]["height"]), (3000, 2000))
        self.assertEqual(results[0]["license"], "NGA Open Access")
        self.assertEqual(
            results[0]["thumb_url"],
            "https://api.nga.gov/iiif/a/full/!1024,1024/0/default.jpg",
        )
        self.assertEqual(
            results[0]["page_url"],
            "https://www.nga.gov/artworks/7-the-liberation-of-saint-peter",
        )

    def test_nga_artwork_url_includes_title_slug_required_by_current_site(self):
        self.assertEqual(
            additional_sources._nga_artwork_url(
                "37134", "Mars and Venus (Mercury and Venus?)"
            ),
            "https://www.nga.gov/artworks/"
            "37134-mars-and-venus-mercury-and-venus",
        )

    def test_gnosis_catalog_unchanged_probe_skips_record_download(self):
        catalog = gnosis_catalog.GnosisCatalog("/tmp/nonexistent-gnosis-probe.json")
        record = gnosis_fuzzy.prepare_record({
            "id": 1, "title": "One", "modified": "2026-08-22T01:02:03",
        })
        catalog.records = [record]
        catalog.latest_modified = "2026-08-22T01:02:03"
        catalog._probe = lambda: ("2026-08-22T01:02:03", 1)
        catalog._fetch_records = lambda *args: self.fail("downloaded unchanged records")
        self.assertEqual(catalog.refresh(), 1)
        self.assertEqual(catalog.last_refresh_mode, "unchanged")

    def test_gnosis_catalog_extracts_explicit_artist_and_artwork_date(self):
        record = gnosis_catalog.wordpress_record({
            "id": 24352,
            "title": {"rendered": "Madonna with Child"},
            "caption": {"rendered": "<p>A devotional painting.</p>"},
            "jetpack_videopress": {
                "description": (
                    "The Madonna and Child by Giovanni Battista Salvi da "
                    "Sassoferrato (1640), showing Mary and the infant Jesus."
                ),
            },
            "media_details": {
                "width": 1200, "height": 1600,
                # WordPress credit can name the uploader/photographer rather
                # than the artist; the curated description is authoritative.
                "image_meta": {"credit": "Example Photographer"},
            },
            "source_url": "https://gnosis.test/madonna.jpg",
            "mime_type": "image/jpeg",
        })
        self.assertEqual(
            record["artist"], "Giovanni Battista Salvi da Sassoferrato"
        )
        self.assertEqual(record["date"], "1640")

    def test_gnosis_catalog_prefers_explicit_image_credit_and_date(self):
        artist, artwork_date = gnosis_catalog.artwork_metadata(
            title="1640-50",
            description="The Virgin in Prayer, an oil painting.",
            credit="Sassoferrato",
            metadata_caption=(
                "Full title: The Virgin in Prayer\r\n"
                "Artist: Sassoferrato\r\nDate made: 1640-50"
            ),
        )
        self.assertEqual(artist, "Sassoferrato")
        self.assertEqual(artwork_date, "1640-50")

    def test_gnosis_catalog_incrementally_merges_modified_records(self):
        catalog = gnosis_catalog.GnosisCatalog("/tmp/nonexistent-gnosis-merge.json")
        old = {"id": 1, "title": "Old", "modified": "2026-08-22T01:00:00"}
        new = {"id": 2, "title": "New", "modified": "2026-08-22T02:00:00"}
        catalog.records = [gnosis_fuzzy.prepare_record(old)]
        catalog.latest_modified = old["modified"]
        catalog._probe = lambda: (new["modified"], 2)
        fetched_after = []
        catalog._fetch_records = lambda timestamp="": fetched_after.append(timestamp) or [new]
        saved = []
        catalog._save = lambda records, latest, total, mode: saved.append(
            (records, latest, total, mode)
        )
        self.assertEqual(catalog.refresh(), 2)
        self.assertTrue(fetched_after[0].startswith("2026-08-22T00:59:58"))
        self.assertEqual({record["id"] for record in saved[0][0]}, {1, 2})
        self.assertEqual(saved[0][3], "incremental")

    def test_gnosis_catalog_refreshes_only_after_a_stale_search(self):
        catalog = gnosis_catalog.GnosisCatalog("/tmp/nonexistent-gnosis-catalog.json")
        starts = []
        original_thread = gnosis_catalog.threading.Thread

        class FakeThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                starts.append(self.kwargs.get("name"))

        gnosis_catalog.threading.Thread = FakeThread
        try:
            catalog.updated_at = time.time()
            self.assertFalse(catalog.refresh_if_stale())
            self.assertEqual(starts, [])
            catalog.updated_at = 0
            catalog.search("peter", 10)
            self.assertEqual(starts, ["gnosis-catalog-refresh"])
            catalog.search("peter", 10)
            self.assertEqual(len(starts), 1)
        finally:
            gnosis_catalog.threading.Thread = original_thread

    def test_gnosis_live_search_expands_escaping_to_escape(self):
        wanted = result("gnosis", "Liberation of Saint Peter", "25340")
        calls = []
        original_standard = server._GNOSIS_STANDARD_SEARCH
        original_live = sources._gnosis_live
        original_fuzzy = server.GNOSIS_CATALOG.search
        server._GNOSIS_STANDARD_SEARCH = lambda *args: []
        sources._gnosis_live = lambda query, need: (
            calls.append(query) or ([wanted] if query == "peter escape prison" else [])
        )
        server.GNOSIS_CATALOG.search = lambda *args: []
        try:
            items = server.search_gnosis("peter escaping prison", 10)
        finally:
            server._GNOSIS_STANDARD_SEARCH = original_standard
            sources._gnosis_live = original_live
            server.GNOSIS_CATALOG.search = original_fuzzy
        self.assertIn("peter escape prison", calls)
        self.assertEqual([item["source_id"] for item in items], ["25340"])

    def test_gnosis_fuzzy_index_still_works_when_wordpress_is_unavailable(self):
        fuzzy = result("gnosis", "Local fuzzy result", "local")
        original_standard = server._GNOSIS_STANDARD_SEARCH
        original_fuzzy = server.GNOSIS_CATALOG.search
        server._GNOSIS_STANDARD_SEARCH = lambda *args: (_ for _ in ()).throw(
            RuntimeError("offline")
        )
        server.GNOSIS_CATALOG.search = lambda *args: [fuzzy]
        try:
            items = server.search_gnosis("misspell", 10)
        finally:
            server._GNOSIS_STANDARD_SEARCH = original_standard
            server.GNOSIS_CATALOG.search = original_fuzzy
        self.assertEqual([item["source_id"] for item in items], ["local"])

    def test_gnosis_fuzzy_search_tolerates_typo_and_missing_diacritics(self):
        records = [{
            "id": 7,
            "title": "Thiền định dưới cây bồ đề",
            "filename": "meditating-buddha.jpg",
            "description": "A Buddhist teacher meditating beneath a sacred tree",
            "page_url": "https://gnosis.test/7",
            "image_url": "https://gnosis.test/7.jpg",
            "thumb_url": "https://gnosis.test/7-small.jpg",
            "width": 1600,
            "height": 1200,
        }]
        original = gnosis_fuzzy._load_records
        gnosis_fuzzy._load_records = lambda path: [
            {**record, "_fields": {
                name: (gnosis_fuzzy._fold(record.get(name)),
                       gnosis_fuzzy._tokens(record.get(name)))
                for name in gnosis_fuzzy.TEXT_COLUMNS if record.get(name)
            }} for record in records
        ]
        try:
            typo = gnosis_fuzzy.search(__file__, "budhist meditating", 10)
            vietnamese = gnosis_fuzzy.search(__file__, "thien dinh", 10)
        finally:
            gnosis_fuzzy._load_records = original
        self.assertEqual(typo[0]["source_id"], "7")
        self.assertEqual(
            typo[0]["description"],
            "A Buddhist teacher meditating beneath a sacred tree",
        )
        self.assertEqual(vietnamese[0]["source_id"], "7")

    def test_gnosis_uses_live_wordpress_before_local_index(self):
        live = result("gnosis", "Fresh WordPress result", "live")
        local = result("gnosis", "Older local result", "local")
        original_live, original_local = sources._gnosis_live, sources._gnosis_local
        sources._gnosis_live = lambda query, need: [live]
        sources._gnosis_local = lambda query, need: [local, live]
        try:
            items = sources.gnosis("subject", 10)
        finally:
            sources._gnosis_live, sources._gnosis_local = original_live, original_local
        self.assertEqual([item["source_id"] for item in items], ["live", "local"])

    def test_gnosis_live_bypasses_stale_cache_and_keeps_english_description(self):
        calls = []
        response = [{
            "id": 25340,
            "title": {"rendered": "Antonio_de_Bellis_-_La_liberazione_di_San_Pietro"},
            "caption": {"rendered": "<p>Vietnamese caption</p>"},
            "description": {"rendered": ""},
            "jetpack_videopress": {
                "description": "The Liberation of St. Peter. Key words: escape, prison."
            },
            "mime_type": "image/webp", "link": "https://gnosis.test/work",
            "source_url": "https://gnosis.test/work.webp",
            "media_details": {"width": 3840, "height": 2673, "sizes": {}},
        }]
        original = sources._get_json
        def fake_get(*args, **kwargs):
            calls.append((args, kwargs))
            return response
        sources._get_json = fake_get
        try:
            item = sources._gnosis_live("peter escape", 10)[0]
        finally:
            sources._get_json = original
        self.assertFalse(calls[0][1]["ttl_ok"])
        self.assertIn("escape", item["medium"])
        self.assertIn("The Liberation of St. Peter", item["description"])
        self.assertEqual((item["width"], item["height"]), (3840, 2673))

    def test_gnosis_live_rejects_krishna_substring_inside_ramakrishna(self):
        response = [{
            "id": 10810,
            "title": {"rendered": "Lecture screenshot"},
            "caption": {"rendered": "<p>Swami Sarvapriyananda</p>"},
            "description": {"rendered": (
                "A Vedanta lecture by a monk of the Ramakrishna Order."
            )},
            "mime_type": "image/jpeg", "link": "https://gnosis.test/10810",
            "source_url": "https://gnosis.test/10810.jpg",
            "media_details": {"width": 1200, "height": 800, "sizes": {}},
        }]
        original = sources._get_json
        sources._get_json = lambda *args, **kwargs: response
        try:
            self.assertEqual(sources._gnosis_live("Krishna", 10), [])
        finally:
            sources._get_json = original

    def test_aic_uses_api_dimensions_cached_iiif_sizes_and_placeholder(self):
        response = {
            "config": {"iiif_url": "https://images.example/iiif/2"},
            "data": [{
                "id": 42, "title": "Tall work", "image_id": "abc",
                "description": "<p>A towering figure fills the narrow canvas.</p>",
                "is_public_domain": True,
                "thumbnail": {"width": 1000, "height": 2000, "lqip": "data:image/gif;base64,x"},
            }],
        }
        wikidata = {"results": {"bindings": [{
            "articId": {"value": "42"},
            "image": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/Tall%20work.jpg"},
        }]}}
        original = sources._get_json
        sources._get_json = lambda url, *args, **kwargs: (
            wikidata if "query.wikidata.org" in url else response
        )
        try:
            item = sources.aic("tall", 1)[0]
        finally:
            sources._get_json = original
        self.assertEqual(item["thumb_url"],
                         "https://commons.wikimedia.org/wiki/Special:FilePath/Tall%20work.jpg?width=843")
        self.assertEqual(item["image_url"],
                         "https://commons.wikimedia.org/wiki/Special:FilePath/Tall%20work.jpg")
        self.assertEqual((item["width"], item["height"]), (1000, 2000))
        self.assertEqual(item["description"], "A towering figure fills the narrow canvas.")
        self.assertTrue(item["placeholder_url"].startswith("data:image/gif"))

    def test_aic_discards_near_zero_score_match_all_fallback(self):
        response = {"data": [{
            "_score": 0.0000245, "id": 11, "title": "Self-Portrait",
            "image_id": "fallback", "is_public_domain": True,
            "thumbnail": {},
        }, {
            "_score": 0.0000240, "id": 27992,
            "title": "A Sunday on La Grande Jatte — 1884",
            "image_id": "fallback-2", "is_public_domain": True,
            "thumbnail": {},
        }]}
        original = sources._get_json
        sources._get_json = lambda *args, **kwargs: response
        try:
            self.assertEqual(sources.aic("samael aun weor", 2), [])
        finally:
            sources._get_json = original

    def test_aic_keeps_genuinely_scored_search_results(self):
        response = {"data": [{
            "_score": 55.6, "id": 16327, "title": "The Annunciation",
            "image_id": "annunciation", "is_public_domain": True,
            "thumbnail": {},
        }]}
        originals = (sources._get_json, sources._aic_commons_images,
                     sources._hf_aic_images_for, sources._wayback_aic_images_for)
        sources._get_json = lambda *args, **kwargs: response
        sources._aic_commons_images = lambda ids: {}
        sources._hf_aic_images_for = lambda ids: {}
        sources._wayback_aic_images_for = lambda artworks: {}
        try:
            items = sources.aic("annunciation", 1)
        finally:
            (sources._get_json, sources._aic_commons_images,
             sources._hf_aic_images_for,
             sources._wayback_aic_images_for) = originals
        self.assertEqual([item["source_id"] for item in items], ["16327"])

    def test_aic_discards_match_all_padding_after_genuine_results(self):
        response = {"data": [{
            "_score": 79.5, "id": 185963, "title": "Cauldron",
            "image_id": "cauldron", "is_public_domain": True,
            "thumbnail": {},
        }, {
            "_score": 0.000024, "id": 16568, "title": "Water Lilies",
            "image_id": "water-lilies", "is_public_domain": True,
            "thumbnail": {},
        }]}
        originals = (sources._get_json, sources._aic_commons_images,
                     sources._hf_aic_images_for, sources._wayback_aic_images_for)
        sources._get_json = lambda *args, **kwargs: response
        sources._aic_commons_images = lambda ids: {}
        sources._hf_aic_images_for = lambda ids: {}
        sources._wayback_aic_images_for = lambda artworks: {}
        try:
            items = sources.aic("Cauldron", 10)
        finally:
            (sources._get_json, sources._aic_commons_images,
             sources._hf_aic_images_for,
             sources._wayback_aic_images_for) = originals
        self.assertEqual([item["source_id"] for item in items], ["185963"])

    def test_aic_discards_high_scoring_single_concept_false_positive(self):
        response = {"data": [{
            "_score": 69.58571, "id": 95, "title": "Daniel Mytens",
            "artist_display": "Paul Pontius", "image_id": "mytens",
            "is_public_domain": True, "thumbnail": {},
        }, {
            "_score": 58.753357, "id": 14556,
            "title": "Auvers, Panoramic View",
            "artist_display": "Paul Cezanne (French, 1839–1906)",
            "description": "A landscape overlooking the countryside.",
            "subject_titles": ["landscapes", "hills", "trees"],
            "image_id": "auvers", "is_public_domain": True,
            "thumbnail": {},
        }, {
            "_score": 61.434586, "id": 80940,
            "title": "Saint Paul Rescued from Prison by an Angel",
            "image_id": "saint-paul", "is_public_domain": True,
            "thumbnail": {},
        }]}
        requested_urls = []
        resolved_ids = []
        originals = (sources._get_json, sources._aic_commons_images,
                     sources._hf_aic_images_for, sources._wayback_aic_images_for)

        def get_json(url, *args, **kwargs):
            requested_urls.append(url)
            return response

        def resolved(ids):
            resolved_ids.append(list(ids))
            return {}

        sources._get_json = get_json
        sources._aic_commons_images = resolved
        sources._hf_aic_images_for = resolved
        sources._wayback_aic_images_for = lambda artworks: {}
        try:
            items = sources.aic("paul escape from prison", 1)
        finally:
            (sources._get_json, sources._aic_commons_images,
             sources._hf_aic_images_for,
             sources._wayback_aic_images_for) = originals

        self.assertEqual([item["source_id"] for item in items], ["80940"])
        self.assertEqual(resolved_ids, [[80940], [80940]])
        self.assertIn("limit=20", requested_urls[0])
        self.assertIn("subject_titles,term_titles", requested_urls[0])

    def test_aic_requires_both_concepts_for_two_concept_query(self):
        self.assertFalse(sources._aic_has_sufficient_concept_coverage(
            "paul prison", {"artist_display": "Paul Cezanne"},
        ))
        self.assertTrue(sources._aic_has_sufficient_concept_coverage(
            "paul prison", {"title": "Paul Released from Prison"},
        ))
        self.assertTrue(sources._aic_has_sufficient_concept_coverage(
            "paul escape from prison",
            {"title": "Liberation of Saint Peter from Prison"},
        ))

    def test_aic_keeps_partial_match_found_in_hidden_controlled_terms(self):
        mary_magdalene = {
            "title": "Mary Magdalene",
            "description": (
                "Mary Magdalene casts a melancholy glance at the viewer."
            ),
            "subject_titles": [
                "Mary Magdalene", "trees", "tree of life", "foliage",
            ],
            "term_titles": ["woman", "religious figures", "tree of life"],
        }
        self.assertTrue(sources._aic_has_sufficient_concept_coverage(
            "Kabbalistic Tree of Life", mary_magdalene,
        ))
        mary_magdalene["subject_titles"].append("Kabbalistic")
        self.assertTrue(sources._aic_has_sufficient_concept_coverage(
            "Kabbalistic Tree of Life", mary_magdalene,
        ))

    def test_aic_display_description_surfaces_hidden_lucifer_subject_term(self):
        record = {
            "description": "Demons on horses climb a mountain.",
            "subject_titles": [
                "satan", "devil/satan/lucifer/beezelbub/mephistopheles",
            ],
            "term_titles": ["panel painting"],
        }
        description = sources._aic_display_description(record, "Lucifer")
        self.assertIn(
            "Subject term — “devil/satan/lucifer/beezelbub/mephistopheles”",
            description,
        )
        self.assertIn("Demons on horses", description)

    def test_aic_single_concept_query_keeps_broad_provider_behavior(self):
        self.assertTrue(sources._aic_has_sufficient_concept_coverage(
            "paul", {"title": "Metadata can be incomplete"},
        ))

    def test_aic_uses_hugging_face_preview_and_preserves_native_dimensions(self):
        response = {
            "config": {"iiif_url": "https://images.example/iiif/2"},
            "data": [{
                "id": 120172, "title": "Penitent Saint Peter", "image_id": "abc",
                "is_public_domain": True,
                "thumbnail": {"width": 1732, "height": 2250, "lqip": "data:image/gif;base64,x"},
            }],
        }
        original_get = sources._get_json
        original_commons = sources._aic_commons_images
        original_hf = sources._hf_aic_images_for
        sources._get_json = lambda *args, **kwargs: response
        sources._aic_commons_images = lambda ids: {}
        sources._hf_aic_images_for = lambda ids: {
            "120172": {"src": "https://hf.test/preview.jpg", "width": 843, "height": 1095}
        }
        try:
            item = sources.aic("peter", 1)[0]
        finally:
            sources._get_json = original_get
            sources._aic_commons_images = original_commons
            sources._hf_aic_images_for = original_hf
        self.assertEqual(item["image_delivery"], "huggingface")
        self.assertEqual(item["image_url"], "https://hf.test/preview.jpg")
        self.assertEqual(
            item["fallback_image_url"],
            "https://images.example/iiif/2/abc/full/!843,843/0/default.jpg",
        )
        self.assertEqual((item["preview_width"], item["preview_height"]), (843, 1095))
        self.assertEqual((item["width"], item["height"]), (1732, 2250))
        self.assertEqual(item["full_resolution_url"],
                         "https://www.artic.edu/artworks/120172")

    def test_aic_hugging_face_row_index_is_packaged(self):
        row_map = json.loads(Path(sources._HF_AIC_ROWS_PATH).read_text())
        self.assertGreater(len(row_map), 50_000)
        self.assertEqual(row_map["3675"], 1195)
        self.assertEqual(row_map["114932"], 41409)

    def test_aic_starts_commons_and_hugging_face_before_waiting(self):
        response = {"data": [{
            "id": 42, "title": "Work", "image_id": "abc",
            "is_public_domain": True, "thumbnail": {},
        }]}
        commons_started = threading.Event()
        hf_started = threading.Event()
        originals = (sources._get_json, sources._aic_commons_images,
                     sources._hf_aic_images_for, sources._wayback_aic_images_for)
        sources._get_json = lambda *args, **kwargs: response

        def commons(ids):
            commons_started.set()
            self.assertTrue(hf_started.wait(1))
            return {}

        def hugging_face(ids):
            hf_started.set()
            self.assertTrue(commons_started.wait(1))
            return {}

        sources._aic_commons_images = commons
        sources._hf_aic_images_for = hugging_face
        sources._wayback_aic_images_for = lambda artworks: {}
        try:
            sources.aic("work", 1)
        finally:
            (sources._get_json, sources._aic_commons_images,
             sources._hf_aic_images_for,
             sources._wayback_aic_images_for) = originals
        self.assertTrue(commons_started.is_set() and hf_started.is_set())

    def test_aic_rejects_monochrome_commons_when_current_aic_image_is_color(self):
        artwork = {
            "id": 36495,
            "thumbnail": {"lqip": "data:image/gif;base64,current-color"},
        }
        original_aic_color = sources._data_image_colorfulness
        original_commons_color = sources._remote_image_colorfulness
        sources._data_image_colorfulness = lambda value: 0.31
        sources._remote_image_colorfulness = lambda value: 0.01
        try:
            accepted = sources._aic_commons_is_current_quality(
                artwork, "https://commons.test/old-monochrome.jpg"
            )
        finally:
            sources._data_image_colorfulness = original_aic_color
            sources._remote_image_colorfulness = original_commons_color
        self.assertFalse(accepted)

    def test_aic_keeps_commons_when_both_reproductions_are_monochrome(self):
        artwork = {"id": 1, "thumbnail": {"lqip": "data:image/gif;base64,bw"}}
        original_aic_color = sources._data_image_colorfulness
        original_commons_color = sources._remote_image_colorfulness
        sources._data_image_colorfulness = lambda value: 0.02
        sources._remote_image_colorfulness = lambda value: 0.01
        try:
            accepted = sources._aic_commons_is_current_quality(
                artwork, "https://commons.test/legitimate-monochrome.jpg"
            )
        finally:
            sources._data_image_colorfulness = original_aic_color
            sources._remote_image_colorfulness = original_commons_color
        self.assertTrue(accepted)

    def test_aic_uses_wayback_when_commons_and_hugging_face_are_unavailable(self):
        response = {
            "config": {"iiif_url": "https://images.example/iiif/2"},
            "data": [{
                "id": 36495, "title": "Liberation of Saint Peter from Prison",
                "image_id": "current-color", "is_public_domain": True,
                "thumbnail": {"width": 7127, "height": 7795, "lqip": "data:x"},
            }],
        }
        originals = (sources._get_json, sources._aic_commons_images,
                     sources._hf_aic_images_for, sources._wayback_aic_images_for)
        sources._get_json = lambda *args, **kwargs: response
        sources._aic_commons_images = lambda ids: {}
        sources._hf_aic_images_for = lambda ids: {}
        sources._wayback_aic_images_for = lambda artworks: {
            "36495": {"src": "https://web.archive.test/color.jpg",
                      "width": 1686, "height": 0}
        }
        try:
            item = sources.aic("peter", 1)[0]
        finally:
            (sources._get_json, sources._aic_commons_images,
             sources._hf_aic_images_for,
             sources._wayback_aic_images_for) = originals
        self.assertEqual(item["image_delivery"], "wayback")
        self.assertEqual(item["image_url"], "https://web.archive.test/color.jpg")
        self.assertEqual(item["preview_width"], 1686)
        self.assertEqual((item["width"], item["height"]), (7127, 7795))

    def test_normalization_keeps_aic_delivery_metadata_for_the_ui(self):
        item = server.normalize_result(result(
            "aic", source_id="120172", image_delivery="huggingface",
            full_resolution_url="https://www.artic.edu/artworks/120172",
            preview_width=843, preview_height=1095,
        ))
        self.assertEqual(item["image_delivery"], "huggingface")
        self.assertEqual((item["preview_width"], item["preview_height"]), (843, 1095))
        self.assertIn("120172", item["full_resolution_url"])
        self.assertEqual(item["preview_click_action"], "visit_website")
        self.assertEqual(item["preview_click_url"], item["page_url"])
        self.assertEqual(item["download_url"], "")

    def test_normalization_visits_page_and_exposes_direct_download_when_available(self):
        item = server.normalize_result(result("met", source_id="17"))
        self.assertEqual(item["preview_click_action"], "visit_website")
        self.assertEqual(item["preview_click_url"], item["page_url"])
        self.assertEqual(item["download_url"], item["image_url"])

    def test_normalization_visits_aic_page_and_exposes_commons_download(self):
        item = server.normalize_result(result(
            "aic", source_id="42", image_delivery="commons",
            full_resolution_url="https://www.artic.edu/artworks/42",
        ))
        self.assertEqual(item["preview_click_action"], "visit_website")
        self.assertEqual(item["preview_click_url"], item["page_url"])
        self.assertEqual(item["download_url"], item["image_url"])

    def test_source_batch_returns_only_the_requested_slice(self):
        items = [result("met", f"Item {index}", str(index)) for index in range(30)]
        adapters = {"met": lambda query, need: items[:need]}
        group = server.search_batch("met", "item", 10, 10, adapters)
        self.assertEqual([item["source_id"] for item in group["results"]],
                         [str(index) for index in range(10, 20)])
        self.assertFalse(group["exhausted"])

    def test_stream_round_starts_every_collection_at_once_with_preview_batches(self):
        selected = ["met", "nga", "getty", "cleveland"]
        session = server.SearchSession("light", selected)
        started = []
        started_lock = threading.Lock()
        all_started = threading.Event()
        release = threading.Event()

        def batch_search(
            source_name, query, offset, batch_size, cancelled=None,
            resolve_dimensions=True,
        ):
            with started_lock:
                started.append((source_name, offset, batch_size))
                if len(started) == len(selected):
                    all_started.set()
            release.wait(2)
            return {
                "source": source_name, "results": [], "error": "",
                "offset": offset, "count": 0, "exhausted": True,
            }

        events = []
        consumer = threading.Thread(
            target=lambda: events.extend(server.stream_search_round(session, batch_search)),
        )
        consumer.start()
        try:
            self.assertTrue(all_started.wait(1))
            self.assertEqual({item[0] for item in started}, set(selected))
            self.assertEqual({item[2] for item in started}, {1})
        finally:
            release.set()
            consumer.join(2)
        self.assertFalse(consumer.is_alive())
        self.assertEqual(events[-1]["type"], "complete")

    def test_stream_round_grows_from_one_to_two_to_four_then_ten(self):
        schedule = (
            (0, 0, 0, 1),
            (1, 1, 1, 2),
            (2, 3, 2, 4),
            (3, 7, 4, server.BATCH_SIZE),
        )
        for rounds, fetched, previous_count, expected_size in schedule:
            with self.subTest(rounds=rounds):
                session = server.SearchSession("light", ["met"])
                session.source_states["met"].update(
                    fetched=fetched,
                    rounds=rounds,
                    last_batch_count=previous_count,
                )
                calls = []

                def batch_search(
                    source_name, query, offset, batch_size, cancelled=None,
                    resolve_dimensions=True,
                ):
                    calls.append((offset, batch_size))
                    return {
                        "source": source_name, "results": [], "error": "",
                        "offset": offset, "count": 0, "exhausted": True,
                    }

                list(server.stream_search_round(session, batch_search))
                self.assertEqual(calls, [(fetched, expected_size)])

    def test_stream_shows_metadata_before_dimension_and_model_enrichment(self):
        session = server.SearchSession("light", ["met"])
        item = server.normalize_result(result(
            "met", "Immediate", "fast", width=0, height=0,
        ))
        enrichment_started = threading.Event()
        release_enrichment = threading.Event()
        original_enrichment = server.score_search_results

        def batch_search(
            source_name, query, offset, batch_size, cancelled=None,
            resolve_dimensions=True,
        ):
            self.assertFalse(resolve_dimensions)
            return {
                "source": source_name, "results": [item], "error": "",
                "offset": offset, "count": 1, "exhausted": False,
            }

        def blocked_enrichment(query, items):
            enrichment_started.set()
            release_enrichment.wait(2)
            return items

        server.score_search_results = blocked_enrichment
        events = server.stream_search_round(session, batch_search)
        try:
            first = next(events)
            self.assertEqual(first["type"], "snapshot")
            self.assertEqual(first["snapshot"]["results"][0]["title"], "Immediate")
            self.assertTrue(enrichment_started.wait(1))
            self.assertFalse(release_enrichment.is_set())
        finally:
            release_enrichment.set()
            events.close()
            server.score_search_results = original_enrichment

    def test_fast_collection_advances_without_waiting_for_slow_collection(self):
        session = server.SearchSession("light", ["met", "nga"])
        release_slow = threading.Event()
        fast_finished = threading.Event()
        fast_batch_sizes = []

        def batch_search(
            source_name, query, offset, batch_size, cancelled=None,
            resolve_dimensions=True,
        ):
            if source_name == "met":
                release_slow.wait(2)
                return {
                    "source": source_name, "results": [], "error": "",
                    "offset": offset, "count": 0, "exhausted": True,
                }
            fast_batch_sizes.append(batch_size)
            exhausted = len(fast_batch_sizes) == 4
            if exhausted:
                fast_finished.set()
            return {
                "source": source_name, "results": [], "error": "",
                "offset": offset, "count": batch_size, "exhausted": exhausted,
            }

        events = []
        consumer = threading.Thread(
            target=lambda: events.extend(server.stream_search_round(session, batch_search)),
        )
        consumer.start()
        try:
            self.assertTrue(fast_finished.wait(1))
            self.assertEqual(fast_batch_sizes, [1, 2, 4, server.BATCH_SIZE])
            self.assertTrue(consumer.is_alive())
        finally:
            release_slow.set()
            consumer.join(2)
        self.assertFalse(consumer.is_alive())

    def test_identical_live_batches_are_coalesced_and_cached(self):
        original = sources.ADAPTERS["met"]
        calls = []
        adapter_started = threading.Event()
        release_adapter = threading.Event()

        def adapter(query, need):
            calls.append((query, need))
            adapter_started.set()
            release_adapter.wait(1)
            return [result("met", "Cached", "cache-test")]

        sources.ADAPTERS["met"] = adapter
        with server.SEARCH_BATCH_CACHE_LOCK:
            server.SEARCH_BATCH_CACHE.clear()
            server.SEARCH_BATCH_INFLIGHT.clear()
        groups = []
        try:
            threads = [threading.Thread(
                target=lambda: groups.append(server.search_batch(
                    "met", "cache test", 0,
                )),
            ) for _ in range(2)]
            threads[0].start()
            self.assertTrue(adapter_started.wait(1))
            threads[1].start()
            release_adapter.set()
            for thread in threads:
                thread.join(2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(groups), 2)
            groups[0]["results"][0]["title"] = "Mutated"
            self.assertEqual(groups[1]["results"][0]["title"], "Cached")
        finally:
            release_adapter.set()
            sources.ADAPTERS["met"] = original
            with server.SEARCH_BATCH_CACHE_LOCK:
                server.SEARCH_BATCH_CACHE.clear()
                server.SEARCH_BATCH_INFLIGHT.clear()

    def test_cancelled_batch_stops_before_adapter_work(self):
        calls = []
        with self.assertRaises(server.SearchCancelled):
            server.search_batch(
                "met", "item", 0, adapters={"met": lambda query, need: calls.append(1)},
                cancelled=lambda: True,
            )
        self.assertEqual(calls, [])

    def test_batch_keeps_images_regardless_of_dimensions(self):
        items = [result("met", "Large", "1", width=1200, height=400),
                 result("met", "Small", "2", width=500, height=600)]
        adapters = {"met": lambda query, need: items}
        group = server.search_batch("met", "item", 0, 10, adapters)
        self.assertEqual([item["title"] for item in group["results"]], ["Large", "Small"])

    def test_aic_keeps_native_and_preview_dimensions(self):
        item = result(
            "aic", "Saint Peter", "93049", width=4077, height=6116,
            image_delivery="huggingface", preview_width=843, preview_height=1265,
        )
        group = server.search_batch(
            "aic", "saint peter", 0, 10,
            {"aic": lambda query, need: [item]},
        )
        self.assertEqual([result["source_id"] for result in group["results"]], ["93049"])
        self.assertEqual(group["results"][0]["width"], 4077)
        self.assertEqual(group["results"][0]["preview_width"], 843)


class HighResolutionCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_cache_directory = server.HIGH_RES_CACHE_DIR
        server.HIGH_RES_CACHE_DIR = Path(self.temporary_directory.name)
        with server.HIGH_RES_CACHE_LOCK:
            server.HIGH_RES_CACHE_INFLIGHT.clear()

    def tearDown(self):
        server.HIGH_RES_CACHE_DIR = self.original_cache_directory
        with server.HIGH_RES_CACHE_LOCK:
            server.HIGH_RES_CACHE_INFLIGHT.clear()
        self.temporary_directory.cleanup()

    def test_second_request_reads_full_image_from_disk(self):
        item = server.normalize_result(result("met", source_id="cached"))
        downloads = []

        def downloader(value):
            downloads.append(value["id"])
            return b"\xff\xd8\xfffull-image", "image/jpeg"

        first = server.cached_high_res_image(item, downloader)
        second = server.cached_high_res_image(item, downloader)

        self.assertFalse(first[2])
        self.assertTrue(second[2])
        self.assertEqual(second[:2], first[:2])
        self.assertEqual(downloads, [item["id"]])

    def test_cache_keeps_only_five_most_recent_images(self):
        items = [
            server.normalize_result(result("met", source_id=str(index)))
            for index in range(6)
        ]
        downloader = lambda item: (b"\x89PNG\r\n\x1a\nimage", "image/png")

        for item in items:
            server.cached_high_res_image(item, downloader)
            time.sleep(0.002)

        cached_files = list(server.HIGH_RES_CACHE_DIR.glob("*.png"))
        oldest_key = server._high_res_cache_key(items[0]["image_url"])
        self.assertEqual(len(cached_files), 5)
        self.assertFalse((server.HIGH_RES_CACHE_DIR / f"{oldest_key}.png").exists())


class SimilarityTests(unittest.TestCase):
    def feature(self, color, gray, bits, aspect=0.0):
        color = np.array(color, dtype="float32")
        gray = np.array(gray, dtype="float32")
        color /= np.linalg.norm(color)
        gray /= np.linalg.norm(gray)
        return {"color": color, "gray": gray,
                "dhash": np.array(bits, dtype=bool), "aspect": aspect}

    def test_identical_visual_features_rank_above_different_features(self):
        target = self.feature([1, 0], [1, 0], [1, 0, 1, 0])
        same = self.feature([1, 0], [1, 0], [1, 0, 1, 0])
        different = self.feature([0, 1], [0, 1], [0, 1, 0, 1], 1.2)
        self.assertGreater(
            visual_similarity.feature_similarity(target, same),
            visual_similarity.feature_similarity(target, different),
        )

    def test_high_siglip_match_accepts_scan_color_shift(self):
        original = visual_similarity.result_feature_similarity
        visual_similarity.result_feature_similarity = lambda first, second: 0.74
        item = {"width": 1200, "height": 900}
        try:
            self.assertTrue(visual_similarity.likely_same_image(item, item, 0.983))
            self.assertFalse(visual_similarity.likely_same_image(item, item, 0.960))
        finally:
            visual_similarity.result_feature_similarity = original

    def test_redraw_requires_exceptionally_close_structure(self):
        original = visual_similarity.result_feature_similarity
        item = {"width": 1200, "height": 900}
        try:
            visual_similarity.result_feature_similarity = lambda first, second: 0.960
            self.assertTrue(visual_similarity.likely_same_image(item, item, 0.918))
            visual_similarity.result_feature_similarity = lambda first, second: 0.940
            self.assertFalse(visual_similarity.likely_same_image(item, item, 0.918))
        finally:
            visual_similarity.result_feature_similarity = original

    def test_siglip_download_retries_original_after_thumbnail_failure(self):
        original = semantic_embeddings._download_image
        calls = []
        semantic_embeddings._download_image = lambda url: calls.append(url) or (
            "image" if url.endswith("original.jpg") else None
        )
        try:
            image = semantic_embeddings._download_item_image({
                "thumb_url": "https://i0.wp.com/preview.jpg",
                "image_url": "https://gnosisvn.org/original.jpg",
            })
            self.assertEqual(image, "image")
            self.assertEqual(calls, [
                "https://i0.wp.com/preview.jpg",
                "https://gnosisvn.org/original.jpg",
            ])
        finally:
            semantic_embeddings._download_image = original

    def test_similarity_bounds_perceptual_download_candidate_pool(self):
        items = [
            server.normalize_result(result("met", "Target", "target"), 0),
            *[
                server.normalize_result(
                    result("met", f"Candidate {index}", str(index)), index,
                )
                for index in range(60)
            ],
        ]
        original_item_feature = visual_similarity._item_feature
        original_image_similarity = semantic_embeddings.image_similarity
        calls = []
        visual_similarity._item_feature = lambda item: calls.append(item["id"]) or None
        semantic_embeddings.image_similarity = lambda first, second: (
            1.0 - int(second["source_id"]) / 100
        )
        try:
            ranked = visual_similarity.rank_similar(items, items[0]["id"], limit=12)
        finally:
            visual_similarity._item_feature = original_item_feature
            semantic_embeddings.image_similarity = original_image_similarity
        self.assertEqual(len(ranked), 12)
        # One target plus only the nominated candidate pool may request a
        # perceptual fingerprint, regardless of total search size.
        self.assertLessEqual(
            len(calls), visual_similarity.MAX_PERCEPTUAL_CANDIDATES + 1,
        )


if __name__ == "__main__":
    unittest.main()
