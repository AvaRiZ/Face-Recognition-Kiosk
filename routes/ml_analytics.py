
import math
import threading
import time
import warnings
import struct
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from db import connect as db_connect, table_columns
from routes.forecasting import run_all_forecasts


_FORECAST_CACHE_TTL_SECONDS = 60
_forecast_cache_lock = threading.Lock()
_forecast_cache = {
    "signature": None,
    "computed_at": 0.0,
    "result": None,
}

def _coerce_confidence(value):
    """Mirror of the one in routes.py — needed here for standalone use."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        try:
            if len(raw) == 4:
                return struct.unpack("f", raw)[0]
            if len(raw) == 8:
                return struct.unpack("d", raw)[0]
        except struct.error:
            pass
        try:
            return float(raw.decode("utf-8", errors="ignore"))
        except (ValueError, TypeError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except (ValueError, TypeError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    text = str(value).strip()
    return text


def _to_builtin(value):
    """Recursively convert NumPy/Pandas scalars into JSON-safe Python types."""
    if isinstance(value, dict):
        return {key: _to_builtin(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_builtin(item) for item in value]
    if isinstance(value, tuple):
        return [_to_builtin(item) for item in value]

    scalar_item = getattr(value, "item", None)
    if callable(scalar_item):
        try:
            return _to_builtin(scalar_item())
        except (TypeError, ValueError):
            pass

    return value


def _build_forecast_signature(daily_df):
    if daily_df.empty:
        return ()
    return tuple(
        (row.date.strftime("%Y-%m-%d"), int(row.count))
        for row in daily_df.itertuples(index=False)
    )


def _get_cached_forecast(daily_df, today):
    signature = _build_forecast_signature(daily_df)
    now = time.time()

    with _forecast_cache_lock:
        cache_hit = (
            _forecast_cache["result"] is not None
            and _forecast_cache["signature"] == signature
            and (now - float(_forecast_cache["computed_at"])) < _FORECAST_CACHE_TTL_SECONDS
        )
        if cache_hit:
            return _forecast_cache["result"], {
                "status": "hit",
                "ttl_seconds": _FORECAST_CACHE_TTL_SECONDS,
                "age_seconds": round(now - float(_forecast_cache["computed_at"]), 1),
                "computed_at": datetime.fromtimestamp(
                    float(_forecast_cache["computed_at"]),
                    ZoneInfo("Asia/Manila"),
                ).isoformat(),
            }

    forecast_result = run_all_forecasts(daily_df, today)
    computed_at = time.time()

    with _forecast_cache_lock:
        _forecast_cache["signature"] = signature
        _forecast_cache["computed_at"] = computed_at
        _forecast_cache["result"] = forecast_result

    return forecast_result, {
        "status": "miss",
        "ttl_seconds": _FORECAST_CACHE_TTL_SECONDS,
        "age_seconds": 0,
        "computed_at": datetime.fromtimestamp(
            computed_at,
            ZoneInfo("Asia/Manila"),
        ).isoformat(),
    }


def _get_user_program_expr(conn):
    try:
        user_columns = table_columns(conn, "users")
    except Exception:
        user_columns = set()
    if "course" in user_columns:
        return "NULLIF(TRIM(u.course), '')"
    if "program" in user_columns:
        return "NULLIF(TRIM(u.program), '')"
    return "NULL"


def _fetch_live_rows(conn, cursor):
    user_program_expr = _get_user_program_expr(conn)
    try:
        event_columns = table_columns(conn, "recognition_events")
    except Exception:
        event_columns = set()

    try:
        user_columns = table_columns(conn, "users")
    except Exception:
        user_columns = set()
    has_users_table = bool(user_columns)

    if event_columns:
        decision_filter = ""
        if "decision" in event_columns:
            decision_filter = "WHERE COALESCE(e.decision, 'allowed') = 'allowed'"
        event_time_expr = "COALESCE(e.captured_at, e.ingested_at)"
        join_users_clause = ""
        sr_expr = "COALESCE(e.sr_code, '')"
        name_expr = "'-'"
        program_expr = "NULL"
        if has_users_table:
            join_users_clause = (
                "LEFT JOIN users u "
                "ON (e.user_id IS NOT NULL AND u.user_id = e.user_id) "
                "OR (e.sr_code IS NOT NULL AND u.sr_code = e.sr_code)"
            )
            sr_expr = "COALESCE(e.sr_code, u.sr_code, '')"
            name_expr = "COALESCE(u.name, '-')"
            program_expr = user_program_expr

        cursor.execute(
            f"""
            SELECT
                {sr_expr} AS sr_code,
                {name_expr} AS name,
                {program_expr} AS program,
                NULL AS gender,
                NULL AS year_level,
                e.confidence,
                {event_time_expr} AS event_time,
                'live' AS source
            FROM recognition_events e
            {join_users_clause}
            {decision_filter}
            ORDER BY event_time ASC
            """
        )
        event_rows = cursor.fetchall()
        if event_rows:
            return event_rows

    if has_users_table:
        try:
            legacy_columns = table_columns(conn, "recognition_log")
        except Exception:
            legacy_columns = set()
        if legacy_columns:
            cursor.execute(
                f"""
                SELECT
                    u.sr_code,
                    u.name,
                    {user_program_expr} AS program,
                    NULL AS gender,
                    NULL AS year_level,
                    r.confidence,
                    r.timestamp AS event_time,
                    'live' AS source
                FROM recognition_log r
                JOIN users u ON r.user_id = u.user_id
                ORDER BY r.timestamp ASC
                """
            )
            return cursor.fetchall()

    return []


def run_ml_analytics(db_path):
    """
    Full ML analytics pipeline.
    Reads from recognition_events (fallback: recognition_log) + imported_logs,
    runs ARIMA, Linear Regression, K-Means, Chi-square,
    Pearson Correlation, and ANOVA.
    Returns a dict ready to be passed to jsonify().
    """
    import numpy as np
    import pandas as pd
    from scipy import stats as scipy_stats
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import r2_score

    warnings.filterwarnings("ignore")

    conn = db_connect(db_path)
    c    = conn.cursor()

    # ── Ensure imported_logs exists ────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS imported_logs (
        import_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sr_code TEXT NOT NULL, name TEXT, gender TEXT,
        program TEXT, year_level TEXT, timestamp TEXT NOT NULL,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        import_batch TEXT)""")
    conn.commit()

    # ══════════════════════════════════════════════════════════
    # STAGE 1 — RAW DATA COLLECTION
    # ══════════════════════════════════════════════════════════
    live_rows = _fetch_live_rows(conn, c)

    c.execute("""
        SELECT sr_code, name, NULLIF(TRIM(program),'') AS program,
               NULLIF(TRIM(gender),'') AS gender,
               NULLIF(TRIM(year_level),'') AS year_level,
               0.85 AS confidence, timestamp, 'imported' AS source
        FROM imported_logs ORDER BY timestamp ASC
    """)
    imported_rows = c.fetchall()

    try:
        c.execute("SELECT COUNT(*) FROM users")
        total_students = c.fetchone()[0]
    except Exception:
        total_students = 0
    conn.close()

    all_raw    = live_rows + imported_rows
    total_raw  = len(all_raw)
    total_live = len(live_rows)
    total_imp  = len(imported_rows)

    # ══════════════════════════════════════════════════════════
    # STAGE 2 — DATA CLEANING
    # ══════════════════════════════════════════════════════════
    OPEN_HOUR, CLOSE_HOUR = 7, 19
    removed_conf = removed_hrs = removed_dup = 0

    after_conf = []
    for row in all_raw:
        sr, name, prog, gender, yr, conf, ts, src = row
        ts_text = _timestamp_to_text(ts)
        if src == "live":
            cv = _coerce_confidence(conf)
            if cv is None or cv < 0.50:
                removed_conf += 1
                continue
        else:
            cv = float(conf) if conf else 0.85
        after_conf.append((sr, name, prog or "", gender or "",
                           yr or "", cv, ts_text, src))

    after_hrs = []
    for row in after_conf:
        sr, name, prog, gender, yr, cv, ts, src = row
        if ts and len(ts) == 10:
            ts = ts + " 08:00:00"
        try:
            hour = int((ts or "")[11:13])
            if hour < OPEN_HOUR or hour >= CLOSE_HOUR:
                removed_hrs += 1
                continue
        except Exception:
            removed_hrs += 1
            continue
        after_hrs.append((sr, name, prog, gender, yr, cv, ts, src))

    seen    = set()
    cleaned = []
    for row in sorted(after_hrs, key=lambda x: x[6]):
        sr, name, prog, gender, yr, cv, ts, src = row
        day = (ts or "")[:10]
        key = (sr, day)
        if not sr or not day or key in seen:
            removed_dup += 1
            continue
        seen.add(key)
        cleaned.append((sr, name, prog, gender, yr, cv, ts, src))

    total_cleaned = len(cleaned)
    data_quality  = {
        "total_raw":           total_raw,
        "total_live":          total_live,
        "total_imported":      total_imp,
        "total_cleaned":       total_cleaned,
        "total_removed":       total_raw - total_cleaned,
        "removed_low_conf":    removed_conf,
        "removed_outside_hrs": removed_hrs,
        "removed_duplicates":  removed_dup,
        "quality_score":       round(total_cleaned / total_raw * 100, 1) if total_raw else 0,
    }

    # ══════════════════════════════════════════════════════════
    # STAGE 3 — BUILD DATAFRAME
    # ══════════════════════════════════════════════════════════
    records = []
    for sr, name, prog, gender, yr, cv, ts, src in cleaned:
        day = (ts or "")[:10]
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        records.append({
            "sr_code":    sr,
            "name":       name or "-",
            "program":    prog or "Unknown",
            "gender":     (gender or "Unknown").strip().title(),
            "year_level": yr or "Unknown",
            "confidence": cv,
            "date":       day,
            "hour":       int((ts or "")[11:13]) if ts and len(ts) >= 13 else 8,
            "dow":        d.weekday(),
            "source":     src,
        })

    df = pd.DataFrame(records)
    if df.empty:
        return _to_builtin({
            "error": "No data available after cleaning.",
            "data_quality": data_quality,
        })

    daily_df    = df.groupby("date").size().reset_index(name="count")
    daily_df["date"] = pd.to_datetime(daily_df["date"])
    daily_df    = daily_df.sort_values("date")
    student_df  = df.groupby("sr_code").agg(
        name        = ("name",    "first"),
        program     = ("program", "first"),
        gender      = ("gender",  "first"),
        year_level  = ("year_level", "first"),
        total_visits= ("date",    "count"),
    ).reset_index()

    # ══════════════════════════════════════════════════════════
    # STAGE 4 — EDA
    # ══════════════════════════════════════════════════════════
    counts_arr = daily_df["count"].values.astype(float)
    mean_v   = round(float(np.mean(counts_arr)),   1) if len(counts_arr) else 0
    median_v = round(float(np.median(counts_arr)), 1) if len(counts_arr) else 0
    std_v    = round(float(np.std(counts_arr)),    1) if len(counts_arr) else 0
    max_v    = int(np.max(counts_arr))              if len(counts_arr) else 0
    min_v    = int(np.min(counts_arr))              if len(counts_arr) else 0

    descriptive_stats = {
        "mean_daily_visits":   mean_v,
        "median_daily_visits": median_v,
        "std_dev":             std_v,
        "max_daily_visits":    max_v,
        "min_daily_visits":    min_v,
        "total_visit_days":    len(daily_df),
    }

    dow_labels  = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    dow_avg_df  = df.groupby("dow").size().reset_index(name="cnt")
    day_counts  = df.groupby(["dow","date"]).size().reset_index().groupby("dow").size()
    dow_averages = []
    for d_i in range(7):
        mask = dow_avg_df["dow"] == d_i
        if mask.any() and d_i in day_counts.index:
            total = int(dow_avg_df[mask]["cnt"].values[0])
            days  = int(day_counts[d_i])
            dow_averages.append(round(total / days, 1))
        else:
            dow_averages.append(0)

    today     = datetime.now(ZoneInfo("Asia/Manila")).date()
    start_30d = today - timedelta(days=29)
    date_map  = dict(zip(daily_df["date"].dt.date.astype(str),
                         daily_df["count"].astype(int)))
    last_30_labels = [(start_30d + timedelta(days=i)).isoformat()[5:] for i in range(30)]
    last_30_counts = [date_map.get((start_30d + timedelta(days=i)).isoformat(), 0) for i in range(30)]

    # Gender / year level
    gender_dist   = df.groupby("gender")["sr_code"].nunique().reset_index()
    gender_dist.columns = ["label","count"]
    gender_data   = gender_dist[gender_dist["count"] > 0].to_dict("records")

    yl_dist       = df.groupby("year_level")["sr_code"].nunique().reset_index()
    yl_dist.columns = ["label","count"]
    year_level_data = yl_dist.sort_values("label").to_dict("records")

    # Program distribution
    program_dist = df.groupby("program")["sr_code"].nunique().reset_index()
    program_dist.columns = ["program","count"]
    program_distribution = program_dist.sort_values("count", ascending=False).head(8).to_dict("records")

    # Peak hours
    hour_counts = df.groupby("hour").size()
    peak_hours  = [int(hour_counts.get(h, 0)) for h in range(24)]

    # ══════════════════════════════════════════════════════
    # STAGE 5a — MULTI-MODEL FORECASTING
    # ARIMA · SARIMA · Prophet · Holt-Winters
    # ══════════════════════════════════════════════════════
    forecast_result = {}
    forecast_cache = {
        "status": "disabled",
        "ttl_seconds": _FORECAST_CACHE_TTL_SECONDS,
        "age_seconds": 0,
        "computed_at": None,
    }
    try:
        forecast_result, forecast_cache = _get_cached_forecast(daily_df, today)
    except Exception as e:
        # Full fallback to weighted moving average so the UI still has 7 days to render.
        last_28 = [
            date_map.get((today - timedelta(days=i)).isoformat(), 0)
            for i in range(27, -1, -1)
        ]
        recent_7 = last_28[-7:] if len(last_28) >= 7 else last_28
        base = float(np.mean(recent_7)) if recent_7 and any(recent_7) else mean_v
        sd_f = float(np.std(recent_7)) if recent_7 and any(recent_7) else std_v

        fc_labels = []
        fc_values = []
        fc_lower = []
        fc_upper = []
        for i in range(1, 8):
            fd = today + timedelta(days=i)
            fc_labels.append(fd.strftime("%a %m/%d"))
            fc_values.append(max(0, round(base)))
            fc_lower.append(max(0, round(base - sd_f)))
            fc_upper.append(max(0, round(base + sd_f)))

        forecast_result = {
            "primary_forecast": {
                "model": "Moving Average",
                "labels": fc_labels,
                "values": fc_values,
                "lower": fc_lower,
                "upper": fc_upper,
                "method": "7-day weighted moving average (all models failed)",
                "metrics": {"mae": None, "rmse": None, "mape": None},
                "interpretation": (
                    f"Advanced forecasting models were unavailable, so a "
                    f"moving-average fallback was used. Error: {e}"
                ),
            },
            "all_forecasts": [],
            "comparison":    [],
            "best_model":    "Moving Average",
            "errors":        {"all": str(e)},
            "comparison_interpretation": "Models could not be evaluated.",
        }
        forecast_cache = {
            "status": "fallback",
            "ttl_seconds": _FORECAST_CACHE_TTL_SECONDS,
            "age_seconds": 0,
            "computed_at": datetime.now(ZoneInfo("Asia/Manila")).isoformat(),
        }

    # ══════════════════════════════════════════════════════════
    # STAGE 5b — LINEAR REGRESSION
    # ══════════════════════════════════════════════════════════
    regression = {}
    regression_interpretation = ""
    try:
        trend_counts = np.array(last_30_counts, dtype=float)
        X      = np.arange(len(trend_counts)).reshape(-1, 1)
        y      = trend_counts
        lr     = LinearRegression().fit(X, y)
        y_pred = lr.predict(X)
        r2     = r2_score(y, y_pred)
        slope  = float(lr.coef_[0])

        regression = {
            "slope":      round(slope, 4),
            "intercept":  round(float(lr.intercept_), 2),
            "r2":         round(r2, 4),
            "r2_pct":     round(r2 * 100, 1),
            "trend":      "increasing" if slope > 0.05 else "decreasing" if slope < -0.05 else "stable",
            "fitted":     [round(float(v), 1) for v in y_pred],
            "counts":     [int(v) for v in trend_counts],
            "labels":     last_30_labels,
        }
        direction = regression["trend"]
        regression_interpretation = (
            f"Linear regression shows a {direction} trend (slope={round(slope,4)} visits/day). "
            f"R²={round(r2,4)} means the model explains {round(r2*100,1)}% of daily visit variance. "
            + ("Strong linear fit." if r2 > 0.5 else
               "Low R² — non-linear factors (academic events, holidays) likely drive variability.")
        )
    except Exception as e:
        regression_interpretation = f"Linear regression failed: {e}"

    # ══════════════════════════════════════════════════════════
    # STAGE 5c — K-MEANS CLUSTERING
    # ══════════════════════════════════════════════════════════
    clustering = {}
    clustering_interpretation = ""
    try:
        if len(student_df) >= 3:
            student_hours = df.groupby("sr_code")["hour"].mean().reset_index()
            student_hours.columns = ["sr_code","avg_hour"]

            all_days    = (daily_df["date"].max() - daily_df["date"].min()).days
            total_weeks = max(1, math.ceil(all_days / 7))

            sdf = student_df.merge(student_hours, on="sr_code", how="left")
            sdf["weekly_avg"] = sdf["total_visits"] / total_weeks
            sdf["avg_hour"]   = sdf["avg_hour"].fillna(10)

            X_raw    = sdf[["total_visits","weekly_avg","avg_hour"]].values
            X_scaled = StandardScaler().fit_transform(X_raw)

            k_range  = range(2, min(6, len(sdf)))
            inertias = []
            for k in k_range:
                km = KMeans(n_clusters=k, random_state=42, n_init=10)
                km.fit(X_scaled)
                inertias.append(km.inertia_)

            best_k   = 3 if len(sdf) >= 6 else 2
            km_final = KMeans(n_clusters=best_k, random_state=42, n_init=10)
            sdf["cluster"] = km_final.fit_predict(X_scaled)

            cluster_means = sdf.groupby("cluster")["total_visits"].mean().sort_values(ascending=False)
            labels_map    = {}
            label_names   = ["High frequency","Moderate frequency","Low frequency"]
            for rank, (cl, _) in enumerate(cluster_means.items()):
                labels_map[cl] = label_names[rank] if rank < len(label_names) else f"Cluster {rank+1}"
            sdf["cluster_label"] = sdf["cluster"].map(labels_map)

            cluster_summary = []
            for cl_label in label_names[:best_k]:
                group = sdf[sdf["cluster_label"] == cl_label]
                if len(group) == 0:
                    continue
                members = group[["name","sr_code","program","total_visits","weekly_avg"]].head(10).copy()
                members["weekly_avg"] = members["weekly_avg"].round(2)
                cluster_summary.append({
                    "label":        cl_label,
                    "count":        len(group),
                    "avg_visits":   round(float(group["total_visits"].mean()), 1),
                    "avg_weekly":   round(float(group["weekly_avg"].mean()), 2),
                    "avg_hour":     round(float(group["avg_hour"].mean()), 1),
                    "top_programs": group["program"].value_counts().head(3).index.tolist(),
                    "members":      members.to_dict("records"),
                })

            clustering = {
                "k":               best_k,
                "total_weeks":     total_weeks,
                "inertia":         [round(v, 2) for v in inertias],
                "k_range":         list(k_range),
                "cluster_summary": cluster_summary,
            }
            clustering_interpretation = (
                f"K-Means (k={best_k}) grouped {len(sdf)} students by visit behavior. "
                + " ".join([
                    f"'{s['label']}': {s['count']} students, avg {s['avg_visits']} visits ({s['avg_weekly']}×/week)."
                    for s in cluster_summary
                ])
            )
    except Exception as e:
        clustering_interpretation = f"K-Means clustering failed: {e}"

    # ══════════════════════════════════════════════════════════
    # STAGE 5d — CHI-SQUARE TEST
    # ══════════════════════════════════════════════════════════
    chi_square = {}
    chi_square_interpretation = ""
    try:
        median_v2 = student_df["total_visits"].median()
        student_df["visit_group"] = student_df["total_visits"].apply(
            lambda v: "High" if v >= median_v2 else "Low"
        )
        contingency = pd.crosstab(student_df["program"], student_df["visit_group"])
        if contingency.shape[0] >= 2 and contingency.shape[1] >= 2:
            chi2, p_val, dof, _ = scipy_stats.chi2_contingency(contingency)
            significant = p_val < 0.05
            chi_square  = {
                "chi2": round(float(chi2), 4), "p_value": round(float(p_val), 4),
                "dof":  int(dof), "significant": significant, "alpha": 0.05,
                "table":   contingency.reset_index().to_dict("records"),
                "columns": ["program"] + list(contingency.columns),
            }
            chi_square_interpretation = (
                f"Chi-square test (χ²={round(chi2,4)}, df={dof}, p={round(p_val,4)}): "
                + ("Significant association found — visit frequency differs across programs."
                   if significant else
                   "No significant association — visit frequency is similar across programs.")
            )
    except Exception as e:
        chi_square_interpretation = f"Chi-square test failed: {e}"

    # ══════════════════════════════════════════════════════════
    # STAGE 5e — PEARSON CORRELATION
    # ══════════════════════════════════════════════════════════
    correlation = {}
    correlation_interpretation = ""
    try:
        daily_df["dow_num"] = daily_df["date"].dt.dayofweek
        r_dow,   p_dow   = scipy_stats.pearsonr(daily_df["dow_num"], daily_df["count"])
        r_trend, p_trend = scipy_stats.pearsonr(np.arange(len(daily_df)), daily_df["count"])

        def _strength(r):
            ar = abs(r)
            if ar >= 0.7: return "strong"
            if ar >= 0.4: return "moderate"
            if ar >= 0.2: return "weak"
            return "negligible"

        correlation = {
            "dow_vs_count":   {"r": round(float(r_dow),4),   "p": round(float(p_dow),4),
                               "significant": p_dow<0.05,    "strength": _strength(r_dow)},
            "trend_vs_count": {"r": round(float(r_trend),4), "p": round(float(p_trend),4),
                               "significant": p_trend<0.05,  "strength": _strength(r_trend)},
        }
        correlation_interpretation = (
            f"Day-of-week vs visits: r={round(r_dow,4)} ({_strength(r_dow)}, "
            f"{'significant' if p_dow<0.05 else 'not significant'}). "
            f"Time trend vs visits: r={round(r_trend,4)} ({_strength(r_trend)}, "
            f"{'significant' if p_trend<0.05 else 'not significant'}). "
            f"Overall library usage is {'growing' if r_trend>0 else 'declining'} over time."
        )
    except Exception as e:
        correlation_interpretation = f"Pearson correlation failed: {e}"

    # ══════════════════════════════════════════════════════════
    # STAGE 5f — ONE-WAY ANOVA
    # ══════════════════════════════════════════════════════════
    anova = {}
    anova_interpretation = ""
    try:
        program_groups = [
            g["total_visits"].values
            for _, g in student_df.groupby("program")
            if len(g) >= 2
        ]
        if len(program_groups) >= 2:
            f_stat, p_val = scipy_stats.f_oneway(*program_groups)
            significant   = p_val < 0.05
            group_means   = (
                student_df.groupby("program")["total_visits"]
                .agg(["mean","std","count"])
                .round(2).reset_index()
            )
            group_means.columns = ["program","mean_visits","std_visits","n"]
            group_means = group_means.sort_values("mean_visits", ascending=False)
            top_prog    = group_means.iloc[0]["program"] if len(group_means) else "—"

            anova = {
                "f_stat":      round(float(f_stat), 4),
                "p_value":     round(float(p_val), 4),
                "significant": significant,
                "alpha":       0.05,
                "n_groups":    len(program_groups),
                "group_means": group_means.to_dict("records"),
            }
            anova_interpretation = (
                f"One-way ANOVA (F={round(f_stat,4)}, p={round(p_val,4)}): "
                + (f"Significant differences found across {len(program_groups)} programs. "
                   f"'{top_prog}' has the highest mean visits."
                   if significant else
                   f"No significant difference in visit frequency across {len(program_groups)} programs.")
            )
    except Exception as e:
        anova_interpretation = f"ANOVA failed: {e}"

    # ══════════════════════════════════════════════════════════
    # STAGE 5g — RULE-BASED SEGMENTATION
    # ══════════════════════════════════════════════════════════
    sorted_days = sorted(date_map.items())
    if sorted_days:
        first_d     = date.fromisoformat(sorted_days[0][0])
        last_d      = date.fromisoformat(sorted_days[-1][0])
        total_weeks = max(1, math.ceil((last_d - first_d).days / 7))
    else:
        total_weeks = 1

    regular = []; occasional = []; rare = []
    for _, row in student_df.iterrows():
        wa    = round(row["total_visits"] / total_weeks, 2)
        entry = {
            "name":         row["name"],
            "sr_code":      row["sr_code"],
            "program":      row["program"],
            "gender":       row.get("gender", "—"),
            "year_level":   row.get("year_level", "—"),
            "total_visits": int(row["total_visits"]),
            "weekly_avg":   wa,
        }
        if wa >= 3:   regular.append(entry)
        elif wa >= 1: occasional.append(entry)
        else:         rare.append(entry)

    for lst in [regular, occasional, rare]:
        lst.sort(key=lambda x: x["total_visits"], reverse=True)

    segmentation = {
        "total_weeks":      total_weeks,
        "regular_count":    len(regular),
        "occasional_count": len(occasional),
        "rare_count":       len(rare),
        "regular":          regular[:20],
        "occasional":       occasional[:20],
        "rare":             rare[:20],
        "segment_labels":   ["Regular (3+/wk)","Occasional (1-2/wk)","Rare (<1/wk)"],
        "segment_counts":   [len(regular), len(occasional), len(rare)],
        "segment_colors":   ["#198754","#ffc107","#dc3545"],
    }

    # ══════════════════════════════════════════════════════════
    # STAGE 5h — ANOMALY DETECTION (Z-score)
    # ══════════════════════════════════════════════════════════
    dow_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    anomalies = []
    if std_v > 0:
        for ds, cnt in sorted_days:
            z = (cnt - mean_v) / std_v
            if abs(z) >= 2.0:
                try:
                    dow_name = dow_names[date.fromisoformat(ds).weekday()]
                except ValueError:
                    dow_name = ""
                anomalies.append({
                    "date":      ds,
                    "day":       dow_name,
                    "count":     int(cnt),
                    "z_score":   round(z, 2),
                    "type":      "spike" if z > 0 else "drop",
                    "deviation": f"{abs(round(z,1))}× std dev {'above' if z>0 else 'below'} mean",
                })
    anomalies.sort(key=lambda x: abs(x["z_score"]), reverse=True)

    # ══════════════════════════════════════════════════════════
    # RETURN FULL PAYLOAD
    # ══════════════════════════════════════════════════════════
    return _to_builtin({
        # Pipeline metadata
        "data_quality":       data_quality,
        "descriptive_stats":  descriptive_stats,
        "total_students":     total_students,
        "total_cleaned_logs": total_cleaned,
        # EDA
        "dow_labels":           dow_labels,
        "dow_averages":         dow_averages,
        "last_30_labels":       last_30_labels,
        "last_30_counts":       last_30_counts,
        "program_distribution": program_distribution,
        "peak_hours":           peak_hours,
        "gender_data":          gender_data,
        "year_level_data":      year_level_data,
        # ML models
        "forecast":              forecast_result.get("primary_forecast", {}),
        "all_forecasts":         forecast_result.get("all_forecasts", []),
        "forecast_comparison":   forecast_result.get("comparison", []),
        "best_forecast_model":   forecast_result.get("best_model", ""),
        "forecast_errors":       forecast_result.get("errors", {}),
        "forecast_warnings":     forecast_result.get("warnings", {}),
        "forecast_successful_models": forecast_result.get("successful_models", 0),
        "forecast_attempted_models":  forecast_result.get("attempted_models", 0),
        "forecast_cache":        forecast_cache,
        "forecast_comparison_interpretation": forecast_result.get("comparison_interpretation", ""),
        "regression":           regression,
        "regression_interpretation": regression_interpretation,
        "clustering":           clustering,
        "clustering_interpretation": clustering_interpretation,
        "chi_square":           chi_square,
        "chi_square_interpretation": chi_square_interpretation,
        "correlation":          correlation,
        "correlation_interpretation": correlation_interpretation,
        "anova":                anova,
        "anova_interpretation": anova_interpretation,
        # Segmentation & anomalies
        "segmentation": segmentation,
        "anomalies":    anomalies,
    })
