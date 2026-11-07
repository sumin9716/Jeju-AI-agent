from jeju_agent.catalog import Catalog, expand_query


def test_catalog_loads_all_rows():
    c = Catalog()
    assert len(c) == 1259
    assert "인구, 생활" in c.categories()


def test_search_finds_floating_population_first():
    c = Catalog()
    hits = c.search("읍면동 유동인구", top_k=3)
    assert hits and hits[0].no == 753


def test_search_synonyms_and_filters():
    c = Catalog()
    assert expand_query("날씨")[:1] == ["날씨"] and "기상" in expand_query("날씨")
    api_only = c.search("버스 정류소", only_api=True, top_k=5)
    assert api_only and all("api" in h.data_type.lower() for h in api_only)
    cat = c.search("카드", category="소비, 경제", top_k=5)
    assert cat and all(h.category == "소비, 경제" for h in cat)


def test_get_entry():
    c = Catalog()
    e = c.get(756)
    assert e is not None and "기상" in e.name
    assert c.get(999999) is None
