# core/cleaner.py
import re
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KEEP_CODE_BLOCKS = True
MIN_CHUNK_CHARS = 80

HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'",
    "&nbsp;": " ", "&#x27;": "'", "&#x2F;": "/",
}

BILL_BLOCK_RE = re.compile(
    r"\*\*Note:\*\* Bill isn't perfect\..*?Privacy Policy\.\]\([^)]+\)\n?",
    re.DOTALL,
)
BILL_INTRO_RE = re.compile(
    r"Hi! I'm Bill! You can ask me all about the Plaid API\. Try asking questions like:\n?"
)
NAV_RE = re.compile(
    r"(?:Search\ or\ Ask.?\ a\ Question\n?|Close\ search\ modal\n?|^Markdown\n|^\[Log\ in\]\([^\)]+\)\n|^\[Get\ API\ Keys\]\([^\)]+\)\n|^\[Plaid\.com\]\([^\)]+\)\n|^Open\ nav\n)",
    re.VERBOSE | re.MULTILINE,
)
IMAGE_RE = re.compile(r'\(An image of "[^"]*"\)\n?')
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
UNICODE_FIXES = [
    (re.compile(r"Â\u00a0"), " "),
    (re.compile(r"Â "), " "),
    (re.compile(r"Â$", re.MULTILINE), ""),
    (re.compile(r"â€[\u201c\u201d]"), "\u2014"),
    (re.compile(r"â€™"), "\u2019"),
    (re.compile(r"â€˜"), "\u2018"),
    (re.compile(r"â€œ"), "\u201c"),
    (re.compile(r"â€"), "\u201d"),
]

TP_SKIP = {
    "ABOUT THE TUTORIAL", "AUDIENCE", "PREREQUISITES", "COPYRIGHT",
    "TABLE OF CONTENTS", "TUTORIALSPOINT",
    "2. RESTFUL WEB SERVICES – ENVIRONMENT SETUP",
    "SETUP JAVA DEVELOPMENT KIT", "SETUP ECLIPSE IDE",
    "SETUP JERSEY FRAMEWORK LIBRARIES", "SETUP APACHE TOMCAT",
    "3. RESTFUL WEB SERVICES – FIRST APPLICATION",
}

REST_SKIP = {
    "CONTENTS", "REFERENCES", "WHERE TO GO FROM HERE?",
}


class Cleaner:

    # --- shared utilities ---

    def _normalize(self, text):
        if not text:
            return ""
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _decode_html(self, text):
        if not text:
            return ""
        for entity, char in HTML_ENTITIES.items():
            text = text.replace(entity, char)
        text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
        text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
        return text

    def _fix_unicode(self, text):
        if not text:
            return ""
        for pattern, replacement in UNICODE_FIXES:
            text = pattern.sub(replacement, text)
        return text

    def _remove_tags(self, text, *tags):
        if not text:
            return ""
        for tag in tags:
            text = re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL)
        return text

    def _remove_page_numbers(self, text):
        if not text:
            return ""
        text = re.sub(r"<page_?number>[ivxlcdmIVXLCDM\d]+</page_?number>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<pagenumber>[ivxlcdmIVXLCDM\d]+</pagenumber>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
        return text

    def _remove_urls(self, text):
        if not text:
            return ""
        text = re.sub(r"\s*\(https?://[^\)]{1,300}\)", "", text)
        text = re.sub(r"https?://\S{4,}", "", text)
        return text

    def _clean_prose(self, text):
        if not text:
            return ""
        text = re.sub(r"`([^`\n]+)`", r"\1", text)
        text = re.sub(r"\*{1,2}([^\*\n]+)\*{1,2}", r"\1", text)
        text = re.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", "", text)
        return text.strip()

    def _process_code_blocks(self, text):
        if not text:
            return ""
        code_fence = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
        parts, last_end = [], 0
        for m in code_fence.finditer(text):
            prose = self._clean_prose(text[last_end:m.start()])
            if prose:
                parts.append(prose)
            if KEEP_CODE_BLOCKS:
                code = m.group(1).rstrip()
                if code.strip():
                    parts.append(f"[CODE]\n{code}\n[/CODE]")
            last_end = m.end()
        remaining = self._clean_prose(text[last_end:])
        if remaining:
            parts.append(remaining)
        return "\n\n".join(parts).strip()

    def _table_to_plain(self, match):
        try:
            table_html = match.group(0)
            def clean_cell(c):
                c = re.sub(r"<br\s*/?>", " ", c, flags=re.IGNORECASE)
                c = re.sub(r"<[^>]+>", "", c)
                return re.sub(r"\s+", " ", c).strip()
            has_header = bool(re.search(r"<th", table_html, re.IGNORECASE))
            rows_raw = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL | re.IGNORECASE)
            rows = []
            for row_html in rows_raw:
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.IGNORECASE)
                cells = [clean_cell(c) for c in cells if clean_cell(c)]
                if cells:
                    rows.append(" | ".join(cells))
            if not rows:
                return ""
            if has_header and len(rows) > 1:
                return rows[0] + "\n" + "-" * len(rows[0]) + "\n" + "\n".join(rows[1:])
            return "\n".join(rows)
        except Exception as e:
            logger.warning(f"Table conversion failed: {e}")
            return ""

    def _convert_tables(self, text):
        if not text:
            return ""
        return re.sub(r"<table>.*?</table>", self._table_to_plain, text, flags=re.DOTALL | re.IGNORECASE)

    def _split_sections(self, text):
        if not text:
            return []
        matches = list(HEADING_RE.finditer(text))
        if not matches:
            logger.warning("No section headings found — returning whole text as one section")
            return [{"title": "content", "body": text.strip()}]
        sections = []
        for i, m in enumerate(matches):
            title = m.group(2).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append({"title": title, "body": text[start:end].strip()})
        return sections

    def _build_chunks(self, sections, skip_set):
        chunks = []
        for sec in sections:
            try:
                upper = sec["title"].strip().upper()
                if any(upper.startswith(s) for s in skip_set):
                    continue
                body = self._process_code_blocks(sec["body"])
                body = re.sub(r"\n{3,}", "\n\n", body)
                if not body or len(body) < MIN_CHUNK_CHARS:
                    continue
                chunks.append(f"SECTION: {sec['title']}\n\n{body}")
            except Exception as e:
                logger.warning(f"Skipping section '{sec.get('title', 'unknown')}': {e}")
                continue
        return chunks

    def _finalize(self, text):
        if not text:
            return ""
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # --- corpus-specific clean methods ---
    # each returns a list of clean text chunks
    # add a new method here for each new corpus (finicity, stripe, etc.)

    def clean_plaid(self, text):
        try:
            text = self._normalize(text)
            text = self._fix_unicode(text)
            text = BILL_BLOCK_RE.sub("", text)
            text = BILL_INTRO_RE.sub("", text)
            text = NAV_RE.sub("", text)
            text = IMAGE_RE.sub("", text)
            text = re.sub(r'\\([\[\]()!*_`\-.#>|{}])', r"\1", text)
            text = re.sub(r'(https?://[^\s)]+?)(?:/index)?\.html\.md(?=[)\s#]|$)', r"\1", text)
            text = re.sub(r"(?m)^---\s*$\n?", "\n", text)
            text = self._finalize(text)
            return [text] if text else []
        except Exception as e:
            logger.error(f"clean_plaid failed: {e}")
            return []

    def clean_stackoverflow(self, text):
        try:
            text = self._normalize(text)
            text = self._decode_html(text)
            divider = re.compile(r"\n={40,}\n")
            sections = divider.split(text)
            chunks = []
            for section in sections:
                try:
                    section = section.strip()
                    if not section:
                        continue
                    q_marker = re.match(r"\[Q#(\d+)\]\s*(.+)", section)
                    if not q_marker:
                        continue
                    if re.search(r"\[No answer available", section, re.IGNORECASE):
                        continue
                    title = q_marker.group(2).strip()
                    q_split = re.split(r"\nQUESTION:\n", section, maxsplit=1)
                    if len(q_split) < 2:
                        continue
                    a_split = re.split(r"\nANSWER[^\n]*:\n", q_split[1], maxsplit=1)
                    question = self._process_code_blocks(a_split[0])
                    answer = self._process_code_blocks(a_split[1]) if len(a_split) > 1 else ""
                    if not question or len(question + answer) < MIN_CHUNK_CHARS:
                        continue
                    chunk = f"TITLE: {title}\n\nQUESTION:\n{question}"
                    if answer:
                        chunk += f"\n\nANSWER:\n{answer}"
                    chunks.append(chunk)
                except Exception as e:
                    logger.warning(f"Skipping SO entry: {e}")
                    continue
            return chunks
        except Exception as e:
            logger.error(f"clean_stackoverflow failed: {e}")
            return []

    def clean_rest_book(self, text):
        try:
            text = self._normalize(text)
            text = self._remove_tags(text, "img", "signature", "header")
            text = re.sub(r"<mermaid>.*?</mermaid>", "[DIAGRAM]", text, flags=re.DOTALL)
            text = self._remove_page_numbers(text)
            text = self._decode_html(text)
            text = self._convert_tables(text)
            text = re.sub(r"^## Page \d+\s*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"^THE LITTLE BOOK ON REST SERVICES\s*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"^Chapter \d+\s*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"^---+\s*$", "", text, flags=re.MULTILINE)
            text = self._remove_urls(text)
            sections = self._split_sections(text)
            return self._build_chunks(sections, REST_SKIP)
        except Exception as e:
            logger.error(f"clean_rest_book failed: {e}")
            return []

    def clean_tutorialspoint(self, text):
        try:
            text = self._normalize(text)
            text = self._remove_tags(text, "img", "signature", "header", "footer")
            text = re.sub(r"<mermaid>.*?</mermaid>", "[DIAGRAM]", text, flags=re.DOTALL)
            text = self._remove_page_numbers(text)
            text = self._decode_html(text)
            text = self._convert_tables(text)
            text = re.sub(r"^## Page \d+\s*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"^RESTful Web Services\s*$", "", text, flags=re.MULTILINE)
            for pat in [r"^tutorialspoint\s*$", r"^SIMPLY EASY LEARNING\s*$", r"^www\.tutorialspoint\.com\s*$"]:
                text = re.sub(pat, "", text, flags=re.MULTILINE | re.IGNORECASE)
            text = re.sub(r"^[^\n]{3,}\.{5,}.*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"\[([^\]]+)\]\(RESTful[^\)]*\)", r"\1", text)
            text = self._remove_urls(text)
            sections = self._split_sections(text)
            return self._build_chunks(sections, TP_SKIP)
        except Exception as e:
            logger.error(f"clean_tutorialspoint failed: {e}")
            return []