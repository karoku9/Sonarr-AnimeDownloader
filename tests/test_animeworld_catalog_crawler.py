import importlib.util
import pathlib
import tempfile
import unittest

INDEX_PATH=pathlib.Path(__file__).parents[1]/"src/components/backend/animeworld_index.py"
spec=importlib.util.spec_from_file_location("animeworld_index_test",INDEX_PATH)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AnimeWorldIndex=module.AnimeWorldIndex


def listing(*items, sidebar=False):
    cards=[]
    for slug,title in items:
        cards.append(f'<div class="item"><a href="/play/{slug}"><img alt="{title}"></a><a href="/play/{slug}">{title}</a></div>')
    extra='<div id="sidebar"><a href="/play/sidebar.fake">Sidebar</a></div>' if sidebar else ''
    return '<html><body><div class="film-list">'+''.join(cards)+'</div>'+extra+'</body></html>'


class FakeIndex(AnimeWorldIndex):
    CATALOG_TYPES=((0,"Anime"),(2,"ONA"))
    def __init__(self,path,pages,max_pages=10,table=None):
        self.fake_pages=pages
        super().__init__(path,"https://www.animeworld.ac",table=table,max_pages=max_pages)
    def _get_html(self,url,attempts=3):
        if url not in self.fake_pages:
            raise RuntimeError(f"unexpected URL {url}")
        return self.fake_pages[url]


class AnimeWorldCatalogCrawlerTests(unittest.TestCase):
    def make(self,pages,max_pages=10,table=None):
        root=tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        return FakeIndex(pathlib.Path(root.name)/"index.json",pages,max_pages=max_pages,table=table)

    def test_listing_uses_only_film_list_and_deduplicates_cards(self):
        idx=self.make({})
        html=listing(("a.1","A"),("b.2","B"),sidebar=True)
        urls=idx._listing_urls_from_html(html,"https://www.animeworld.ac/filter?type=0&page=1")
        self.assertEqual(urls,["https://www.animeworld.ac/play/a.1","https://www.animeworld.ac/play/b.2"])
    def test_discovers_every_type_until_site_wraps_to_first_page(self):
        base="https://www.animeworld.ac/filter"
        pages={
            f"{base}?type=0&page=1":listing(("a.1","A"),("b.2","B")),
            f"{base}?type=0&page=2":listing(("c.3","C")),
            f"{base}?type=0&page=3":listing(("a.1","A"),("b.2","B")),
            f"{base}?type=2&page=1":listing(("d.4","D")),
            f"{base}?type=2&page=2":listing(("d.4","D")),
        }
        idx=self.make(pages)
        found=idx.discover_index_pages()
        self.assertEqual(len(found),3)
        self.assertEqual(idx._catalog_stats["Anime"]["entries"],3)
        self.assertEqual(idx._catalog_stats["ONA"]["entries"],1)
        self.assertEqual(idx._catalog_stats["Anime"]["pages"],2)
        self.assertEqual(idx._catalog_stats["ONA"]["pages"],1)

    def test_refresh_does_not_merge_stale_table_urls(self):
        base="https://www.animeworld.ac/filter"
        pages={
            f"{base}?type=0&page=1":listing(("live.1","Live")),
            f"{base}?type=0&page=2":listing(("live.1","Live")),
            f"{base}?type=2&page=1":listing(("ona.2","ONA Live")),
            f"{base}?type=2&page=2":listing(("ona.2","ONA Live")),
        }
        table=[{"title":"Stale","seasons":{"1":["https://www.animeworld.ac/play/stale.dead"]}}]
        idx=self.make(pages,table=table)
        data=idx.refresh()
        urls={x["url"] for x in data["entries"]}
        self.assertEqual(urls,{"https://www.animeworld.ac/play/live.1","https://www.animeworld.ac/play/ona.2"})
        self.assertTrue(data["catalog_complete"])
    def test_category_is_preserved_from_filter_branch(self):
        base="https://www.animeworld.ac/filter"
        pages={
            f"{base}?type=0&page=1":listing(("anime.1","Anime A")),
            f"{base}?type=0&page=2":listing(("anime.1","Anime A")),
            f"{base}?type=2&page=1":listing(("ona.2","ONA B")),
            f"{base}?type=2&page=2":listing(("ona.2","ONA B")),
        }
        idx=self.make(pages)
        data=idx.refresh()
        categories={x["title"]:x["category"] for x in data["entries"]}
        self.assertEqual(categories,{"Anime A":"Anime","ONA B":"ONA"})

    def test_refuses_partial_index_when_safety_limit_is_hit(self):
        base="https://www.animeworld.ac/filter"
        pages={
            f"{base}?type=0&page=1":listing(("a.1","A")),
            f"{base}?type=0&page=2":listing(("b.2","B")),
        }
        idx=self.make(pages,max_pages=2)
        with self.assertRaisesRegex(RuntimeError,"refusing partial index"):
            idx.discover_index_pages()


if __name__ == "__main__":
    unittest.main()
