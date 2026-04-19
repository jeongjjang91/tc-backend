import re
import sqlglot
from sqlglot import expressions as exp
from app.shared.exceptions import SQLValidationError


class SQLValidator:
    def __init__(self, whitelist: dict):
        self.whitelist = whitelist
        self.allowed_tables: set[str] = set(whitelist.get("tables", {}).keys())
        self.large_tables: set[str] = set(whitelist.get("large_tables", []))
        self.forbidden: list[str] = whitelist.get("forbidden_functions", [])

    def validate_and_fix(self, sql: str) -> str:
        sql = sql.strip().rstrip(";")

        # 1. 파싱
        try:
            tree = sqlglot.parse_one(sql, dialect="oracle")
        except Exception as e:
            raise SQLValidationError(f"SQL 파싱 실패: {e}", sql)

        # 2. SELECT만 허용
        if not isinstance(tree, exp.Select):
            raise SQLValidationError("SELECT 문만 허용됩니다", sql)

        # 3. 위험 함수 차단
        sql_upper = sql.upper()
        for fn in self.forbidden:
            pattern = fn.replace("_", r"\_").replace("*", ".*")
            if re.search(pattern, sql_upper):
                raise SQLValidationError(f"금지 함수 사용: {fn}", sql)

        # 4. 테이블 화이트리스트
        used_tables = {t.name.upper() for t in tree.find_all(exp.Table)}
        forbidden_tables = used_tables - self.allowed_tables
        if forbidden_tables:
            raise SQLValidationError(
                f"허용되지 않은 테이블: {forbidden_tables}. "
                f"허용 목록: {self.allowed_tables}",
                sql,
            )

        # 5. 컬럼 화이트리스트
        # When only one table is referenced, unqualified columns belong to it
        single_table = next(iter(used_tables)) if len(used_tables) == 1 else None
        for col in tree.find_all(exp.Column):
            col_name = col.name.lower()
            table_name = col.table.upper() if col.table else single_table
            if table_name and table_name in self.whitelist["tables"]:
                allowed_cols = self.whitelist["tables"][table_name]["columns"]
                if col_name not in allowed_cols and col_name != "*":
                    raise SQLValidationError(
                        f"허용되지 않은 컬럼: {table_name}.{col_name}", sql
                    )

        # 6. 대용량 테이블은 WHERE 필수
        for t in used_tables:
            if t in self.large_tables:
                if not tree.find(exp.Where):
                    raise SQLValidationError(
                        f"대용량 테이블 {t}은 WHERE 절이 필요합니다", sql
                    )

        # 7. requires_where_clause
        for t in used_tables:
            tconf = self.whitelist["tables"].get(t, {})
            if tconf.get("requires_where_clause") and not tree.find(exp.Where):
                raise SQLValidationError(
                    f"테이블 {t}은 WHERE 절이 필요합니다", sql
                )

        # 8. ROWNUM 자동 주입
        if not tree.find(exp.Limit) and "ROWNUM" not in sql_upper:
            sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= 1000"

        return sql
