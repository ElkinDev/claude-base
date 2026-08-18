# T-SQL / SQL Server rules (optional stack addendum)

Keep this only if the project writes T-SQL against SQL Server. Reference it from the project's
`CLAUDE.project.md`. These are portable across SQL Server projects.

1. Temp-table existence: use `DROP TABLE IF EXISTS #t` and `IF EXISTS (SELECT 1 FROM ...)`, not
   `IF OBJECT_ID('tempdb..#t') IS NOT NULL`.
2. Normalize empty-string params at the top of report stored procedures: a front end sends `''`, not
   NULL, for unchecked filters. After the DECLAREs, `SET @p = NULLIF(LTRIM(RTRIM(@p)), '')` for every
   VARCHAR param.
3. Null-safe joins between temp tables: one side may emit the empty key as NULL and the other as
   `''`, silently dropping rows. Use `ISNULL(left,'') = ISNULL(right,'')`, or pre-COALESCE in the
   INSERT so a plain `=` stays sargable.
4. Keep temp-table index keys sargable. Never wrap an indexed key column in a function in the JOIN
   predicate. Normalize keys at INSERT, join on bare columns. Fix every column in a composite index.
5. Verify CROSS APPLY / OUTER APPLY correlation-key uniqueness before aggregating. If the outer table
   has multiple rows per key and the APPLY aggregates, it recomputes per row. Pre-compute into a temp
   table with GROUP BY and join.
6. Do not wrap large multi-table INSERTs in IF/ELSE per parameter (plan-cache regressions even on
   byte-equivalent branches). Prefer OR predicates that `OPTION (RECOMPILE)` can constant-fold,
   pre-filtered temp tables joined unconditionally, or dynamic SQL.
7. `SELECT INTO` infers width from the literal. `SELECT '' AS col INTO #t` makes `col` VARCHAR(1); a
   later wider UPDATE aborts. CAST the literal: `CAST('' AS VARCHAR(50)) AS col`.
8. Validate a perf hypothesis with the live plan XML before writing the fix. Walk the operator tree
   (PhysicalOp, estimated vs actual rows, subtree cost, warnings) and confirm the real top-cost
   operator first.

## Unit tests vs production case sensitivity
If unit tests use an in-memory SQLite database, note that SQLite `=` is case-sensitive while SQL
Server is case-insensitive by default. For case-insensitive uniqueness checks, compare with
`.ToLower()` so behavior matches both databases and stays unit-testable.
