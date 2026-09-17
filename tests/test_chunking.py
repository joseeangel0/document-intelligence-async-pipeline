from app.extraction.chunking import chunk_markdown, count_tokens, parse_blocks

DOC = (
    "<!-- page: 1 -->\n\n# Agreement\n\n"
    + "Intro sentence number one is here. " * 60
    + "\n\n## Prices\n\n| Service | Price |\n|---|---|\n"
    + "\n".join(f"| Item {i} | {i}.00 |" for i in range(120))
    + "\n\n<!-- page: 2 -->\n\n## Terms\n\n- Pay in 30 days\n- Interest 1.5%\n\nClosing paragraph."
)


def test_blocks_track_pages_and_heading_path():
    blocks = parse_blocks(DOC)
    kinds = [b.kind for b in blocks]
    assert kinds[:2] == ["heading", "paragraph"] and "table" in kinds and "list" in kinds
    terms = next(b for b in blocks if b.kind == "list")
    assert terms.page == 2 and terms.heading_path == ["Agreement", "Terms"]


def test_chunks_respect_budget_and_keep_provenance():
    chunks = chunk_markdown(DOC, chunk_size=200, overlap=30)
    assert len(chunks) > 3
    assert all(c.token_count <= 200 + 16 for c in chunks)  # heading lines may ride along
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].page_start == 1 and chunks[-1].page_end == 2
    assert chunks[-1].heading_path == ["Agreement", "Terms"]
    for c in chunks:  # offsets point into the Markdown
        assert 0 <= c.char_start < c.char_end <= len(DOC)


def test_tables_split_by_rows_repeat_header_and_never_overlap():
    chunks = [c for c in chunk_markdown(DOC, chunk_size=200, overlap=30) if "table" in c.kinds]
    assert len(chunks) >= 2
    for c in chunks:
        table_lines = [line for line in c.text.splitlines() if line.startswith("|")]
        assert table_lines[0] == "| Service | Price |" and table_lines[1] == "|---|---|"
        assert c.overlap_tokens == 0


def test_prose_overlap_starts_at_word_boundary():
    chunks = chunk_markdown(DOC, chunk_size=120, overlap=20)
    overlapped = [c for c in chunks if c.overlap_tokens]
    assert overlapped
    assert all(c.text.split()[0] in ("Intro", "sentence", "number", "one", "is", "here.") for c in overlapped)


def test_small_document_is_one_chunk_and_tokens_are_real():
    chunks = chunk_markdown("# Title\n\nHello world.", 512, 64)
    assert len(chunks) == 1 and chunks[0].text.startswith("# Title")
    assert count_tokens("Hello world.") == 3  # cl100k_base, baked into the image


def test_empty_and_heading_only():
    assert chunk_markdown("", 512, 64) == []
    assert chunk_markdown("# Only a heading", 512, 64) == []
