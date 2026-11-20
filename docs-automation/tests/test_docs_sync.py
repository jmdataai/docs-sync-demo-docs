"""Self-contained tests: build a tiny fake source tree and docs site, run every step.

    cd docs-automation && python -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docs_sync import __main__ as cli  # noqa: E402
from docs_sync.common import read_json  # noqa: E402


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


class DocsSyncTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        r = self.root
        # --- dbt project
        write(r / "sources/dbt/dbt_project.yml", """
            name: proj
            model-paths: ["models"]
            models:
              proj:
                silver:
                  +materialized: table
        """)
        write(r / "sources/dbt/models/silver/m_list.sql", "select * from {{ source('bronze_uc', 'list_parquet') }}\n")
        write(r / "sources/dbt/models/silver/d_basket.sql",
              "select * from {{ source('bronze_uc', 'basket_parquet') }} join {{ ref ('m_list') }}\n")
        # --- snowflake migrations
        write(r / "sources/sf/V1.1.1__init.sql", """
            CREATE OR REPLACE TABLE f_claim (
                net_rx integer NULL,
                pbr_city_nm varchar(50) NULL
            ) CLUSTER BY (pbr_city_nm) DEFAULT_DDL_COLLATION = 'en-ci';
        """)
        write(r / "sources/sf/V1_2__alter.sql", "ALTER TABLE f_claim ADD pbr_st_cd varchar(3) NULL;\n")
        write(r / "sources/sf/R__SP_FClaim.sql", "COPY INTO f_claim FROM @eag_staging/f_claim/;\n")
        # --- extractor
        write(r / "sources/ext/config/extracts.json", json.dumps(
            {"dList": {"name": "List", "type": "dimension", "sqlPath": "sql/list.sql",
                       "outputTablePath": "/mnt/uclanding/list_parquet"}}))
        write(r / "sources/ext/config/sql_config.yml", "dev:\n  extracts:\n    dList:\n      tableName: idw.d_list\n")
        write(r / "sources/ext/sql/list.sql", "SELECT a, b FROM {{ table_name }}\n")
        # --- tenant api
        write(r / "sources/java/constants/DbConstants.java", """
            public class DbConstants {
                public static final String CITY_FILTER = " AND rc.pbr_city_nm IS NOT NULL ";
            }
        """)
        write(r / "sources/java/repositories/CityRepository.java", """
            public interface CityRepository {
                @Query(nativeQuery = true, value = "SELECT rc.pbr_city_nm, SUM(rc.net_rx) " +
                        "FROM F_CLAIM rc WHERE 1=1 " + CITY_FILTER + "GROUP BY rc.pbr_city_nm")
                List<Row> getCities(@Param("filter") NamedParam filter);
            }
        """)
        # --- docs
        write(r / "docs/P/03_Silver/entities/d_basket.md", """
            # d_basket

            ## Build

            | Property | Value |
            | --- | --- |
            | Materialisation | `view` |

            ## What it reads

            <!-- auto:start reads model=d_basket -->
            old text
            <!-- auto:end -->
        """)
        write(r / "docs/P/03_Silver/entities/m_list.md", "# m_list\n")
        write(r / "docs/P/04_Gold/entities/f_claim.md", """
            # F_CLAIM

            ## Columns

            <!-- auto:start gold-columns table=F_CLAIM -->
            | Column | Type | What it is used for |
            | --- | --- | --- |
            | `pbr_city_nm` | `varchar(50)` | City shown on the chart |
            | `net_rx` | `integer` | Volume |
            <!-- auto:end -->

            The city filter lives in `CityRepository`. See [the basket](../../03_Silver/entities/d_basket.md#what-it-reads).
        """)
        write(r / "docs-automation/config.yml", """
            sources:
              dbt: {project_dir: sources/dbt, manifest: sources/none/manifest.json}
              snowflake: {migrations_dir: sources/sf}
              extractor: {package_dir: sources/ext, env: dev}
              tenant: {java_dir: sources/java, repositories_glob: "repositories/*.java", constants_glob: "constants/*.java"}
            docs: {root: docs, products: [P]}
            facts: {current: docs-automation/facts/current, approved: docs-automation/facts/approved}
            output: {dir: docs-automation/out}
        """)
        self.cfg = str(r / "docs-automation/config.yml")

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_cli(self, *args):
        return cli.main(list(args) + ["--config", self.cfg])

    def test_baseline_then_release(self):
        r = self.root
        self.assertEqual(self.run_cli("all", "--approve"), 0)
        page = (r / "docs/P/03_Silver/entities/d_basket.md").read_text()
        self.assertIn("Bronze sources: `basket_parquet`.", page)
        self.assertIn("Silver models: [`m_list`](m_list.md).", page)  # `ref (` with a space is read
        drift = read_json(r / "docs-automation/out/drift.json")
        self.assertTrue(drift["baseline"])
        self.assertTrue(any("Materialisation says `view`" in i["issue"] for i in drift["page_issues"]))

        # a release: new column, wider type, changed shared filter
        write(r / "sources/sf/V1_3__more.sql", """
            ALTER TABLE f_claim ADD rx_channel_cd varchar(10) NULL;
            ALTER TABLE f_claim ALTER COLUMN pbr_city_nm SET DATA TYPE VARCHAR(80) COLLATE 'en-ci';
        """)
        c = r / "sources/java/constants/DbConstants.java"
        c.write_text(c.read_text().replace("IS NOT NULL", "IS NOT NULL AND rc.pbr_city_nm <> ''"))
        self.assertEqual(self.run_cli("all"), 0)

        gold = (r / "docs/P/04_Gold/entities/f_claim.md").read_text()
        self.assertIn("| `pbr_city_nm` | `varchar(80)` | City shown on the chart |", gold)  # description kept
        self.assertIn("| `rx_channel_cd` | `varchar(10)` | To describe |", gold)
        self.assertLess(gold.index("pbr_city_nm"), gold.index("net_rx"))  # page order kept
        drift = read_json(r / "docs-automation/out/drift.json")
        items = {c["item"]: c for c in drift["changes"]}
        self.assertIn("F_CLAIM", items)
        self.assertEqual(items["F_CLAIM"]["auto_pages"], ["P/04_Gold/entities/f_claim.md"])
        self.assertIn("shared SQL filter CITY_FILTER", items)
        self.assertNotIn("CityRepository.getCities", items)  # rolled up into the filter change
        self.assertIn("P/04_Gold/entities/f_claim.md", items["shared SQL filter CITY_FILTER"]["review_pages"])

    def test_manifest_is_preferred(self):
        r = self.root
        node = {"resource_type": "model", "package_name": "proj", "name": "d_basket",
                "original_file_path": "models/silver/d_basket.sql", "config": {"materialized": "table"},
                "tags": ["group_x"], "raw_code": "select 1",
                "depends_on": {"nodes": ["model.proj.m_list", "source.proj.bronze_uc.basket_parquet"]},
                "columns": {"ID": {"description": "key", "data_type": None}}}
        write(r / "sources/none/manifest.json", json.dumps(
            {"metadata": {"project_name": "proj"}, "nodes": {"model.proj.d_basket": node}}))
        self.run_cli("extract")
        silver = read_json(r / "docs-automation/facts/current/silver.json")
        self.assertEqual(list(silver), ["d_basket"])
        self.assertEqual(silver["d_basket"]["refs"], ["m_list"])
        self.assertEqual(silver["d_basket"]["sources"], ["bronze_uc.basket_parquet"])

    def test_bad_marker_fails_the_run(self):
        r = self.root
        p = r / "docs/P/03_Silver/entities/m_list.md"
        p.write_text("# m_list\n\n<!-- auto:start reads model=does_not_exist -->\nx\n<!-- auto:end -->\n")
        self.assertEqual(self.run_cli("all"), 1)

    def test_broken_link_fails_the_check(self):
        r = self.root
        p = r / "docs/P/03_Silver/entities/m_list.md"
        p.write_text("# m_list\n\nSee [nowhere](missing.md) and [bad anchor](d_basket.md#no-such-heading).\n")
        self.run_cli("extract")
        self.assertEqual(self.run_cli("check"), 1)
        errors = read_json(r / "docs-automation/out/checks.json")["errors"]
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
