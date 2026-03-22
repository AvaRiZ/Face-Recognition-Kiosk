import warnings
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def _next_forecast_dates(anchor_date, steps):
    return [anchor_date + timedelta(days=i + 1) for i in range(steps)]


def _apply_closed_day_rules(labels, values, lower, upper, anchor_date):
    """
    Force weekend forecasts to zero because the library is closed on Saturdays and Sundays.
    """
    adjusted_values = list(values)
    adjusted_lower = list(lower)
    adjusted_upper = list(upper)

    for i in range(len(adjusted_values)):
        forecast_date = anchor_date + timedelta(days=i + 1)
        if forecast_date.weekday() >= 5:
            adjusted_values[i] = 0
            adjusted_lower[i] = 0
            adjusted_upper[i] = 0

    return labels, adjusted_values, adjusted_lower, adjusted_upper
 
 
# ── Evaluation Metrics ────────────────────────────────────────
def _mae(actual, predicted):
    """Mean Absolute Error"""
    pairs = [(a, p) for a, p in zip(actual, predicted) if a is not None and p is not None]
    if not pairs:
        return None
    return round(sum(abs(a - p) for a, p in pairs) / len(pairs), 4)
 
 
def _rmse(actual, predicted):
    """Root Mean Squared Error"""
    pairs = [(a, p) for a, p in zip(actual, predicted) if a is not None and p is not None]
    if not pairs:
        return None
    return round(math.sqrt(sum((a - p) ** 2 for a, p in pairs) / len(pairs)), 4)
 
 
def _mape(actual, predicted):
    """Mean Absolute Percentage Error"""
    pairs = [(a, p) for a, p in zip(actual, predicted) if a is not None and p is not None and a != 0]
    if not pairs:
        return None
    return round(sum(abs((a - p) / a) for a, p in pairs) / len(pairs) * 100, 2)
 
 
def _evaluate(ts_series, model_predictions):
    """
    Compare last 7 days actual vs predicted for error metrics.
    ts_series: pd.Series with datetime index, daily counts
    model_predictions: list of 7 predicted values for last 7 days
    """
    if len(ts_series) < 8:
        return {"mae": None, "rmse": None, "mape": None}
    actual = ts_series.iloc[-7:].values.tolist()
    return {
        "mae":  _mae(actual, model_predictions),
        "rmse": _rmse(actual, model_predictions),
        "mape": _mape(actual, model_predictions),
    }
 
 
# ── ARIMA ─────────────────────────────────────────────────────
def forecast_arima(ts_series, today, steps=7):
    """
    Auto-selects best ARIMA(p,d,q) by minimum AIC.
    Returns forecast dict.
    """
    from statsmodels.tsa.arima.model import ARIMA
 
    best_aic   = float("inf")
    best_order = (1, 1, 1)
 
    for p in range(3):
        for d in range(2):
            for q in range(3):
                try:
                    m = ARIMA(ts_series, order=(p, d, q)).fit()
                    if m.aic < best_aic:
                        best_aic, best_order = m.aic, (p, d, q)
                except Exception:
                    continue
 
    model  = ARIMA(ts_series, order=best_order).fit()
    fc_res = model.get_forecast(steps=steps)
    fc_mean= fc_res.predicted_mean
    fc_ci  = fc_res.conf_int(alpha=0.05)
 
    forecast_dates = _next_forecast_dates(ts_series.index[-1].date(), steps)
    labels, values, lower, upper = [], [], [], []
    for i, fd in enumerate(forecast_dates):
        labels.append(fd.strftime("%a %m/%d"))
        values.append(max(0, round(float(fc_mean.iloc[i]))))
        lower.append(max(0, round(float(fc_ci.iloc[i, 0]))))
        upper.append(max(0, round(float(fc_ci.iloc[i, 1]))))

    labels, values, lower, upper = _apply_closed_day_rules(
        labels, values, lower, upper, ts_series.index[-1].date()
    )
 
    # Back-test on last 7 days for error metrics
    if len(ts_series) >= 8:
        bt_model = ARIMA(ts_series.iloc[:-7], order=best_order).fit()
        bt_fc    = bt_model.get_forecast(steps=7).predicted_mean
        bt_preds = [max(0, round(float(v))) for v in bt_fc]
    else:
        bt_preds = values
 
    metrics = _evaluate(ts_series, bt_preds)
 
    return {
        "model":       "ARIMA",
        "model_detail": f"ARIMA{best_order}",
        "labels":      labels,
        "values":      values,
        "lower":       lower,
        "upper":       upper,
        "aic":         round(model.aic, 2),
        "bic":         round(model.bic, 2),
        "model_order": list(best_order),
        "method":      f"ARIMA{best_order} — auto-selected by minimum AIC ({round(best_aic, 2)})",
        "metrics":     metrics,
        "interpretation": (
            f"ARIMA{tuple(best_order)} was selected from all p,d,q ∈ {{0,1,2}} combinations "
            f"by lowest AIC score of {round(best_aic, 2)}. "
            f"Total forecasted visits: {sum(values)} over 7 days. "
            f"MAE: {metrics['mae']}, RMSE: {metrics['rmse']}, MAPE: {metrics['mape']}%."
        ),
    }
 
 
# ── SARIMA ────────────────────────────────────────────────────
def forecast_sarima(ts_series, today, steps=7):
    """
    SARIMA with weekly seasonality (s=7).
    Auto-selects best (p,d,q)(P,D,Q,7) by AIC from reduced grid.
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX
 
    best_aic   = float("inf")
    best_order = (1, 1, 1)
    best_sorder= (1, 0, 1, 7)
 
    # Reduced grid to keep it fast
    for p in range(3):
        for d in range(2):
            for q in range(3):
                for P in range(2):
                    for Q in range(2):
                        try:
                            m = SARIMAX(
                                ts_series,
                                order=(p, d, q),
                                seasonal_order=(P, 1, Q, 7),
                                enforce_stationarity=False,
                                enforce_invertibility=False,
                            ).fit(disp=False)
                            if m.aic < best_aic:
                                best_aic    = m.aic
                                best_order  = (p, d, q)
                                best_sorder = (P, 1, Q, 7)
                        except Exception:
                            continue
 
    model  = SARIMAX(
        ts_series, order=best_order, seasonal_order=best_sorder,
        enforce_stationarity=False, enforce_invertibility=False,
    ).fit(disp=False)
 
    fc_res = model.get_forecast(steps=steps)
    fc_mean= fc_res.predicted_mean
    fc_ci  = fc_res.conf_int(alpha=0.05)
 
    forecast_dates = _next_forecast_dates(ts_series.index[-1].date(), steps)
    labels, values, lower, upper = [], [], [], []
    for i, fd in enumerate(forecast_dates):
        labels.append(fd.strftime("%a %m/%d"))
        values.append(max(0, round(float(fc_mean.iloc[i]))))
        lower.append(max(0, round(float(fc_ci.iloc[i, 0]))))
        upper.append(max(0, round(float(fc_ci.iloc[i, 1]))))

    labels, values, lower, upper = _apply_closed_day_rules(
        labels, values, lower, upper, ts_series.index[-1].date()
    )
 
    # Back-test
    if len(ts_series) >= 14:
        bt_model = SARIMAX(
            ts_series.iloc[:-7], order=best_order, seasonal_order=best_sorder,
            enforce_stationarity=False, enforce_invertibility=False,
        ).fit(disp=False)
        bt_fc    = bt_model.get_forecast(steps=7).predicted_mean
        bt_preds = [max(0, round(float(v))) for v in bt_fc]
    else:
        bt_preds = values
 
    metrics = _evaluate(ts_series, bt_preds)
    order_str = f"SARIMA{best_order}x{best_sorder}"
 
    return {
        "model":       "SARIMA",
        "model_detail": order_str,
        "labels":      labels,
        "values":      values,
        "lower":       lower,
        "upper":       upper,
        "aic":         round(model.aic, 2),
        "bic":         round(model.bic, 2),
        "model_order": list(best_order),
        "seasonal_order": list(best_sorder),
        "method":      f"{order_str} — weekly seasonality (s=7), auto-selected by AIC",
        "metrics":     metrics,
        "interpretation": (
            f"{order_str} extends ARIMA by modeling the weekly seasonal pattern (s=7), "
            f"capturing the Mon–Fri peak and weekend dip in library visits. "
            f"Selected by lowest AIC of {round(best_aic, 2)}. "
            f"Total forecasted visits: {sum(values)} over 7 days. "
            f"MAE: {metrics['mae']}, RMSE: {metrics['rmse']}, MAPE: {metrics['mape']}%."
        ),
    }
 
 
# ── Prophet ───────────────────────────────────────────────────
def forecast_prophet(ts_series, today, steps=7):
    """
    Meta Prophet with weekly seasonality and Philippine academic holidays.
    Handles missing weekend data gracefully.
    """
    from prophet import Prophet
 
    # Build Prophet DataFrame
    df_prophet = pd.DataFrame({
        "ds": ts_series.index,
        "y":  ts_series.values.astype(float),
    }).reset_index(drop=True)
 
    # Philippine academic holidays (add more as needed)
    holidays_list = []
    for yr in range(df_prophet["ds"].dt.year.min(), today.year + 2):
        holidays_list.extend([
            {"holiday": "New Year",       "ds": f"{yr}-01-01"},
            {"holiday": "Independence",   "ds": f"{yr}-06-12"},
            {"holiday": "Rizal Day",      "ds": f"{yr}-12-30"},
            {"holiday": "Christmas",      "ds": f"{yr}-12-25"},
            {"holiday": "All Saints",     "ds": f"{yr}-11-01"},
            {"holiday": "Labor Day",      "ds": f"{yr}-05-01"},
            {"holiday": "Bonifacio Day",  "ds": f"{yr}-11-30"},
        ])
    holidays_df = pd.DataFrame(holidays_list)
    holidays_df["ds"] = pd.to_datetime(holidays_df["ds"])
 
    model = Prophet(
        weekly_seasonality=True,
        yearly_seasonality=True if len(df_prophet) > 365 else False,
        daily_seasonality=False,
        holidays=holidays_df,
        seasonality_mode="additive",
        changepoint_prior_scale=0.05,
        interval_width=0.95,
    )
    model.fit(df_prophet)
 
    future   = model.make_future_dataframe(periods=steps, freq="D")
    forecast = model.predict(future)
    fc_rows  = forecast.tail(steps)
 
    forecast_dates = _next_forecast_dates(ts_series.index[-1].date(), steps)
    labels, values, lower, upper = [], [], [], []
    for i, (_, row) in enumerate(fc_rows.iterrows()):
        fd = forecast_dates[i]
        labels.append(fd.strftime("%a %m/%d"))
        values.append(max(0, round(float(row["yhat"]))))
        lower.append(max(0, round(float(row["yhat_lower"]))))
        upper.append(max(0, round(float(row["yhat_upper"]))))

    labels, values, lower, upper = _apply_closed_day_rules(
        labels, values, lower, upper, ts_series.index[-1].date()
    )
 
    # Back-test on last 7 days
    metrics = {"mae": None, "rmse": None, "mape": None}
    if len(df_prophet) >= 14:
        try:
            bt_model = Prophet(
                weekly_seasonality=True,
                yearly_seasonality=False,
                daily_seasonality=False,
                holidays=holidays_df,
                seasonality_mode="additive",
                changepoint_prior_scale=0.05,
                interval_width=0.95,
            )
            bt_model.fit(df_prophet.iloc[:-7])
            bt_future   = bt_model.make_future_dataframe(periods=7, freq="D")
            bt_forecast = bt_model.predict(bt_future)
            bt_preds    = [max(0, round(float(v))) for v in bt_forecast.tail(7)["yhat"]]
            metrics     = _evaluate(ts_series, bt_preds)
        except Exception:
            pass
 
    return {
        "model":       "Prophet",
        "model_detail": "Prophet (Meta)",
        "labels":      labels,
        "values":      values,
        "lower":       lower,
        "upper":       upper,
        "aic":         None,
        "bic":         None,
        "model_order": None,
        "method":      "Meta Prophet — weekly seasonality + Philippine academic holidays",
        "metrics":     metrics,
        "interpretation": (
            "Prophet decomposes library visits into trend, weekly seasonality, and holiday effects. "
            "It automatically handles the Mon–Fri peak pattern and suppresses expected drops on "
            "Philippine public holidays. "
            f"Total forecasted visits: {sum(values)} over 7 days. "
            f"MAE: {metrics['mae']}, RMSE: {metrics['rmse']}, MAPE: {metrics['mape']}%."
        ),
    }
 
 
# ── Holt-Winters ──────────────────────────────────────────────
def forecast_holt_winters(ts_series, today, steps=7):
    """
    Triple Exponential Smoothing (Holt-Winters) with weekly seasonality.
    Most interpretable method — good for thesis defense explanation.
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
 
    # Need at least 2 full seasons (14 days) for seasonal Holt-Winters
    if len(ts_series) < 14:
        # Fall back to simple exponential smoothing
        model = ExponentialSmoothing(ts_series, trend="add").fit(optimized=True)
        method_note = "Simple Exponential Smoothing (not enough data for seasonal model)"
        params = {}
    else:
        model = ExponentialSmoothing(
            ts_series,
            trend="add",
            seasonal="add",
            seasonal_periods=7,
            damped_trend=True,
        ).fit(optimized=True, remove_bias=True)
        method_note = "Holt-Winters Triple Exponential Smoothing (trend + weekly seasonality)"
        params = {
            "alpha": round(float(model.params.get("smoothing_level", 0)), 4),
            "beta":  round(float(model.params.get("smoothing_trend", 0)), 4),
            "gamma": round(float(model.params.get("smoothing_seasonal", 0)), 4),
            "phi":   round(float(model.params.get("damping_trend", 1)), 4),
        }
 
    fc = model.forecast(steps)
 
    # Confidence intervals (Holt-Winters doesn't provide them natively)
    # Estimate using residual std dev
    residuals = ts_series.values - model.fittedvalues.values
    resid_std = float(np.std(residuals)) if len(residuals) > 1 else 0
    z95 = 1.96
 
    forecast_dates = _next_forecast_dates(ts_series.index[-1].date(), steps)
    labels, values, lower, upper = [], [], [], []
    for i, fd in enumerate(forecast_dates):
        labels.append(fd.strftime("%a %m/%d"))
        val = max(0, round(float(fc.iloc[i])))
        margin = round(z95 * resid_std * math.sqrt(i + 1))
        values.append(val)
        lower.append(max(0, val - margin))
        upper.append(val + margin)

    labels, values, lower, upper = _apply_closed_day_rules(
        labels, values, lower, upper, ts_series.index[-1].date()
    )
 
    # Back-test
    metrics = {"mae": None, "rmse": None, "mape": None}
    if len(ts_series) >= 14:
        try:
            bt_model = ExponentialSmoothing(
                ts_series.iloc[:-7],
                trend="add", seasonal="add",
                seasonal_periods=7, damped_trend=True,
            ).fit(optimized=True, remove_bias=True)
            bt_preds = [max(0, round(float(v))) for v in bt_model.forecast(7)]
            metrics  = _evaluate(ts_series, bt_preds)
        except Exception:
            pass
 
    return {
        "model":        "Holt-Winters",
        "model_detail": "Holt-Winters (Triple ETS)",
        "labels":       labels,
        "values":       values,
        "lower":        lower,
        "upper":        upper,
        "aic":          None,
        "bic":          None,
        "model_order":  None,
        "params":       params,
        "method":       method_note,
        "metrics":      metrics,
        "interpretation": (
            "Holt-Winters Triple Exponential Smoothing models three components: "
            "level (overall average), trend (growth/decline), and seasonality (weekly cycle). "
            f"Smoothing parameters — α (level): {params.get('alpha','—')}, "
            f"β (trend): {params.get('beta','—')}, "
            f"γ (seasonal): {params.get('gamma','—')}. "
            "Higher α means more weight on recent data; higher γ means stronger seasonality adjustment. "
            f"Total forecasted visits: {sum(values)} over 7 days. "
            f"MAE: {metrics['mae']}, RMSE: {metrics['rmse']}, MAPE: {metrics['mape']}%."
        ),
    }
 
 
# ── Best Model Selector ───────────────────────────────────────
def _pick_best(results):
    """
    Picks the best model by lowest RMSE.
    Falls back to MAE if RMSE is unavailable.
    """
    scored = []
    for r in results:
        m = r.get("metrics", {})
        score = m.get("rmse") or m.get("mae")
        if score is not None:
            scored.append((score, r["model"]))
    if not scored:
        return results[0]["model"] if results else "ARIMA"
    return min(scored, key=lambda x: x[0])[1]
 
 
# ── Main Entry Point ──────────────────────────────────────────
def run_all_forecasts(daily_df, today):
    """
    Runs ARIMA, SARIMA, Prophet, and Holt-Winters on the daily visit series.
    Returns a dict with all model results, comparison table, and best model.
 
    daily_df: pd.DataFrame with columns ["date" (datetime), "count" (int)]
    today: date object
    """
    ts_series = daily_df.set_index("date")["count"].asfreq("D", fill_value=0)
 
    results    = []
    errors     = {}
 
    # ── ARIMA ──────────────────────────────────────────────
    try:
        results.append(forecast_arima(ts_series, today))
    except Exception as e:
        errors["ARIMA"] = str(e)
 
    # ── SARIMA ─────────────────────────────────────────────
    try:
        results.append(forecast_sarima(ts_series, today))
    except Exception as e:
        errors["SARIMA"] = str(e)
 
    # ── Prophet ────────────────────────────────────────────
    try:
        results.append(forecast_prophet(ts_series, today))
    except Exception as e:
        errors["Prophet"] = str(e)
 
    # ── Holt-Winters ───────────────────────────────────────
    try:
        results.append(forecast_holt_winters(ts_series, today))
    except Exception as e:
        errors["Holt-Winters"] = str(e)
 
    # ── Comparison table ───────────────────────────────────
    comparison = []
    for r in results:
        m = r.get("metrics", {})
        comparison.append({
            "model":       r["model"],
            "model_detail":r.get("model_detail", r["model"]),
            "mae":         m.get("mae"),
            "rmse":        m.get("rmse"),
            "mape":        m.get("mape"),
            "aic":         r.get("aic"),
            "bic":         r.get("bic"),
            "total_7d":    sum(r.get("values", [])),
        })
 
    best_model = _pick_best(results)
 
    # Best model's full forecast becomes the "primary" forecast
    primary = next((r for r in results if r["model"] == best_model), results[0] if results else {})
 
    return {
        "primary_forecast": primary,
        "all_forecasts":    results,
        "comparison":       comparison,
        "best_model":       best_model,
        "errors":           errors,
        "comparison_interpretation": _comparison_interpretation(comparison, best_model),
    }
 
 
def _comparison_interpretation(comparison, best_model):
    if not comparison:
        return "No models could be fitted."
 
    best = next((c for c in comparison if c["model"] == best_model), comparison[0])
 
    lines = [
        f"Four forecasting models were evaluated on the last 7 days of actual library visit data. "
        f"The best performing model is <strong>{best_model}</strong> "
    ]
 
    if best.get("rmse") is not None:
        lines.append(f"with the lowest RMSE of {best['rmse']} visits/day.")
    elif best.get("mae") is not None:
        lines.append(f"with the lowest MAE of {best['mae']} visits/day.")
    else:
        lines.append("based on available metrics.")
 
    lines.append(
        " RMSE (Root Mean Squared Error) penalizes large errors more than MAE, making it "
        "the primary selection criterion. MAPE (Mean Absolute Percentage Error) expresses "
        "error as a percentage of actual values, useful for comparing across different scales."
    )
 
    return " ".join(lines)
 
