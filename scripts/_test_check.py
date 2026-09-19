"""Stale-feature doc gate contracts: no network, no credentials, no personal state."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check import (
    HISTORICAL_MARKER,
    HISTORICAL_SECTION_MARKER,
    REMOVED_FEATURES,
    advertised_features,
    strip_markdown_noise,
)


class DocGateContracts(unittest.TestCase):
    """`advertised_features` decides whether a doc still offers a deleted feature."""

    def test_cjk_sentence_without_trailing_space_still_reports(self):
        """A CJK full stop carries no space, so prose segmenting used to miss this."""
        text = "价格不再上涨时才加仓。/analyze 启动深度分析并生成报告。"
        self.assertEqual(advertised_features(text), ["/analyze"])

    def test_ordinary_chinese_phrase_does_not_exempt_the_line(self):
        """不再 is an ordinary market phrase, not an opt-out."""
        self.assertEqual(advertised_features("不再上涨时运行 /advise 获取建议。"), ["/advise"])

    def test_section_marker_exempts_the_list_under_a_heading(self):
        text = ("## Removed in 0.5.0\n"
                f"{HISTORICAL_SECTION_MARKER}\n"
                "\n"
                "- /analyze\n"
                "- /advise\n")
        self.assertEqual(advertised_features(text), [])

    def test_same_section_without_the_marker_reports_every_entry(self):
        text = "## Removed in 0.5.0\n\n- /analyze\n- /advise\n"
        self.assertEqual(advertised_features(text), ["/analyze", "/advise"])

    def test_section_exemption_ends_at_the_next_heading(self):
        text = (f"## History\n{HISTORICAL_SECTION_MARKER}\n"
                "- /debate\n"
                "\n"
                "## Current commands\n"
                "Run /screen to filter candidates.\n")
        self.assertEqual(advertised_features(text), ["/screen"])

    def test_line_marker_exempts_a_removal_note(self):
        text = f"The /analyze command was removed in 0.5.0. {HISTORICAL_MARKER}"
        self.assertEqual(advertised_features(text), [])

    def test_line_marker_exempts_an_abbreviation_that_broke_sentence_splitting(self):
        text = ("We removed several commands, e.g. /analyze and /advise. "
                f"{HISTORICAL_MARKER}")
        self.assertEqual(advertised_features(text), [])

    def test_the_word_historical_alone_is_not_an_exemption(self):
        text = "Historical reflections live in data/memory; run /analyze for deep research."
        self.assertEqual(advertised_features(text), ["/analyze"])

    def test_results_follow_declared_order_without_duplicates(self):
        text = "Use /advise, then /analyze, then /advise again.\nAlso /analyze.\n"
        self.assertEqual(advertised_features(text), ["/analyze", "/advise"])

    def test_every_declared_feature_is_detectable_in_bare_prose(self):
        for marker in REMOVED_FEATURES:
            with self.subTest(marker=marker):
                self.assertEqual(advertised_features(f"Run {marker} now."), [marker])


class DocGateFalsePositiveContracts(unittest.TestCase):
    """Paths, code and links name the old features without offering them."""

    def test_fenced_block_is_not_an_advertisement(self):
        self.assertEqual(advertised_features("```\n/analyze AAPL\n```\nNothing else.\n"), [])

    def test_inline_code_span_is_not_an_advertisement(self):
        self.assertEqual(advertised_features("The `/advise` command was an entry point."), [])

    def test_markdown_link_target_is_not_an_advertisement(self):
        text = "See [commands/analyze.md](.claude/commands/analyze.md) for details."
        self.assertEqual(advertised_features(text), [])

    def test_script_path_does_not_match_a_slash_command(self):
        self.assertEqual(advertised_features("Run scripts/analyze_bars.py first."), [])

    def test_directory_path_does_not_match_a_slash_command(self):
        self.assertEqual(advertised_features("See docs/screenshots/ for images."), [])

    def test_url_ending_in_a_command_name_does_not_match(self):
        self.assertEqual(advertised_features("Visit https://example.com/earnings today."), [])

    def test_longer_agent_name_does_not_match_its_prefix(self):
        self.assertEqual(advertised_features("The portfolio-manager-v2 agent ships."), [])

    def test_bare_agent_name_still_matches(self):
        self.assertEqual(advertised_features("Ask the portfolio-manager agent."), ["portfolio-manager"])


class MarkdownStrippingContracts(unittest.TestCase):
    def test_fenced_and_inline_spans_are_removed(self):
        stripped = strip_markdown_noise("a ```x /analyze y``` b `/advise` c")
        self.assertNotIn("/analyze", stripped)
        self.assertNotIn("/advise", stripped)

    def test_link_target_is_removed_but_link_text_survives(self):
        stripped = strip_markdown_noise("[label](.claude/commands/analyze.md)")
        self.assertEqual(stripped, "[label]()")


if __name__ == "__main__":
    unittest.main()
