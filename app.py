import hmac
import os
import secrets

import pandas as pd
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Load Uber dataset
df = pd.read_csv("dataset/uber_data.csv")

# Clean column names
df.columns = df.columns.str.strip().str.replace(
    "\ufeff", "", regex=False
)

# Clean numeric columns
df["Fare_INR"] = pd.to_numeric(
    df["Fare_INR"], errors="coerce"
).fillna(0)

df["Distance_KM"] = pd.to_numeric(
    df["Distance_KM"], errors="coerce"
).fillna(0)

df["Customer_Rating"] = pd.to_numeric(
    df["Customer_Rating"], errors="coerce"
)

# Clean ride status
df["Ride_Status"] = df["Ride_Status"].astype(str).str.strip().str.lower()


def _admin_login_configured():
    return bool(
        os.environ.get("ADMIN_USERNAME")
        and os.environ.get("ADMIN_PASSWORD")
    )


def _issue_admin_csrf_token():
    token = secrets.token_urlsafe(32)
    session["admin_csrf_token"] = token
    return token


@app.before_request
def require_admin_login():
    if request.endpoint in {"admin_login", "static"}:
        return None
    if session.get("admin_authenticated") is True:
        return None
    return redirect(url_for("admin_login"))


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_authenticated") is True:
        return redirect(url_for("workspace", view="home"))

    error = None
    status_code = 200
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "").encode("utf-8")
        expected_token = session.get("admin_csrf_token", "").encode("utf-8")
        if not expected_token or not hmac.compare_digest(submitted_token, expected_token):
            error = "Your login session expired. Refresh the page and try again."
            status_code = 400
        elif not _admin_login_configured():
            error = (
                "Admin login is not configured. Set ADMIN_USERNAME and "
                "ADMIN_PASSWORD before starting the app."
            )
            status_code = 503
        else:
            expected_username = os.environ["ADMIN_USERNAME"]
            expected_password = os.environ["ADMIN_PASSWORD"]
            submitted_username = request.form.get("username", "")
            submitted_password = request.form.get("password", "")
            username_matches = hmac.compare_digest(
                submitted_username.encode("utf-8"),
                expected_username.encode("utf-8"),
            )
            password_matches = hmac.compare_digest(
                submitted_password.encode("utf-8"),
                expected_password.encode("utf-8"),
            )
            if username_matches and password_matches:
                session.clear()
                session["admin_authenticated"] = True
                _issue_admin_csrf_token()
                return redirect(url_for("workspace", view="home"))
            error = "Invalid username or password."
            status_code = 401

    csrf_token = session.get("admin_csrf_token") or _issue_admin_csrf_token()
    return (
        render_template(
            "admin_login.html",
            csrf_token=csrf_token,
            error=error,
            login_configured=_admin_login_configured(),
        ),
        status_code,
    )


@app.route("/admin-logout", methods=["POST"])
def admin_logout():
    submitted_token = request.form.get("csrf_token", "").encode("utf-8")
    expected_token = session.get("admin_csrf_token", "").encode("utf-8")
    if not expected_token or not hmac.compare_digest(submitted_token, expected_token):
        abort(400)
    session.clear()
    return redirect(url_for("admin_login"))


def _status_matches(series, prefix):
    return series.fillna("").astype(str).str.strip().str.lower().str.startswith(prefix)


def _status_category(value):
    text = str(value).strip().lower() if pd.notna(value) else ""
    if text.startswith("completed"):
        return "completed"
    if text.startswith("cancelled"):
        return "cancelled"
    return "other"


def _workspace_redirect(view):
    if request.args.get("embedded") == "1":
        return None
    return redirect(url_for("workspace", view=view))


@app.route("/workspace")
@app.route("/workspace/<view>")
def workspace(view="home"):
    requested_view = request.args.get("view", view)
    if requested_view not in {
        "home",
        "dashboard",
        "ride-analysis",
        "city",
        "bookings",
        "revenue",
        "drivers",
        "reports",
        "payment",
        "ride-status",
        "live-team",
    }:
        requested_view = "home"
    page_endpoints = {
        "home": url_for("workspace_home"),
        "dashboard": url_for("dashboard", embedded=1),
        "ride-analysis": url_for("ride_analysis", embedded=1),
        "city": url_for("city_analysis", embedded=1),
        "bookings": url_for("bookings", embedded=1),
        "revenue": url_for("revenue", embedded=1),
        "drivers": url_for("drivers", embedded=1),
        "reports": url_for("reports", embedded=1),
        "payment": url_for("payment_analysis", embedded=1),
        "ride-status": url_for("ride_status", embedded=1),
        "live-team": url_for("live_team", embedded=1),
    }
    if requested_view not in page_endpoints:
        return redirect(url_for("workspace", view="home"))
    return render_template(
        "workspace.html",
        initial_view=requested_view,
        page_endpoints=page_endpoints,
        csrf_token=session.get("admin_csrf_token", ""),
    )


@app.route("/workspace-home")
def workspace_home():
    average_rating = df["Customer_Rating"].mean()
    return render_template(
        "home.html",
        total_rides=len(df),
        total_revenue=round(float(df["Fare_INR"].sum()), 2),
        total_distance=round(float(df["Distance_KM"].sum()), 2),
        average_rating=round(float(average_rating), 1) if pd.notna(average_rating) else None,
    )


@app.route("/")
def home():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))

    return redirect(url_for("workspace", view="home"))


@app.route("/dashboard")
def dashboard():
    redirect_response = _workspace_redirect("dashboard")
    if redirect_response is not None:
        return redirect_response

    total_rides = len(df)
    total_revenue = round(df["Fare_INR"].sum(), 2)
    completed_rides = int(_status_matches(df["Ride_Status"], "completed").sum())
    cancelled_rides = int(_status_matches(df["Ride_Status"], "cancelled").sum())
    completed_fares = df.loc[_status_matches(df["Ride_Status"], "completed"), "Fare_INR"]
    average_rating = df["Customer_Rating"].mean()

    dated_rides = df.assign(_date=pd.to_datetime(df["Date"], errors="coerce"))
    monthly = (
        dated_rides.dropna(subset=["_date"])
        .assign(_month=lambda rides: rides["_date"].dt.to_period("M"))
        .groupby("_month")
        .agg(rides=("Ride_ID", "count"), revenue=("Fare_INR", "sum"))
        .sort_index()
    )

    status_counts = df["Ride_Status"].value_counts()
    top_cities = df["City"].value_counts().head(6)
    payment_counts = df["Payment_Method"].value_counts()
    recent_rides = (
        dated_rides.sort_values("_date", ascending=False)
        .head(8)
        .assign(Date=lambda rides: rides["_date"].dt.strftime("%d %b %Y"))
        .to_dict("records")
    )

    return render_template(
        "dashboard.html",
        total_rides=total_rides,
        total_revenue=total_revenue,
        average_fare=round(float(completed_fares.mean()), 2) if not completed_fares.empty else 0,
        average_rating=round(float(average_rating), 1) if pd.notna(average_rating) else None,
        completed_rides=completed_rides,
        cancelled_rides=cancelled_rides,
        completion_rate=round(completed_rides / total_rides * 100, 1) if total_rides else 0,
        city_count=df["City"].nunique(),
        month_labels=[period.strftime("%b %Y") for period in monthly.index],
        monthly_rides=monthly["rides"].astype(int).tolist(),
        monthly_revenue=monthly["revenue"].round(2).tolist(),
        status_labels=[status.replace("_", " ").title() for status in status_counts.index],
        status_values=status_counts.astype(int).tolist(),
        city_labels=top_cities.index.tolist(),
        city_values=top_cities.astype(int).tolist(),
        payment_labels=payment_counts.index.tolist(),
        payment_values=payment_counts.astype(int).tolist(),
        recent_rides=recent_rides,
    )


@app.route("/ride-analysis")
def ride_analysis():
    redirect_response = _workspace_redirect("ride-analysis")
    if redirect_response is not None:
        return redirect_response

    ride_df = df.copy()
    ride_df["_date"] = pd.to_datetime(ride_df["Date"], errors="coerce")
    years = sorted(ride_df["_date"].dropna().dt.year.unique().astype(int).tolist(), reverse=True)
    months = list(range(1, 13))
    ride_types = sorted(ride_df["Ride_Type"].dropna().astype(str).unique().tolist())
    ride_statuses = sorted(ride_df["Ride_Status"].dropna().astype(str).unique().tolist())

    selected_year = request.args.get("year", "all")
    if selected_year != "all" and selected_year not in {str(year) for year in years}:
        selected_year = "all"
    selected_month = request.args.get("month", "all")
    if selected_month != "all" and selected_month not in {str(month) for month in months}:
        selected_month = "all"
    selected_ride_type = request.args.get("ride_type", "all")
    if selected_ride_type not in ride_types:
        selected_ride_type = "all"
    selected_status = request.args.get("status", "all")
    if selected_status not in ride_statuses:
        selected_status = "all"

    if selected_year != "all":
        ride_df = ride_df[ride_df["_date"].dt.year == int(selected_year)]
    if selected_month != "all":
        ride_df = ride_df[ride_df["_date"].dt.month == int(selected_month)]
    if selected_ride_type != "all":
        ride_df = ride_df[ride_df["Ride_Type"].astype(str) == selected_ride_type]
    if selected_status != "all":
        ride_df = ride_df[ride_df["Ride_Status"].astype(str) == selected_status]

    total_rides = len(ride_df)
    total_revenue = float(ride_df["Fare_INR"].sum())
    average_fare = float(ride_df["Fare_INR"].mean()) if total_rides else 0
    average_rating = (
        round(float(ride_df["Customer_Rating"].mean()), 1)
        if ride_df["Customer_Rating"].notna().any()
        else None
    )
    completed_rides = int(_status_matches(ride_df["Ride_Status"], "completed").sum())
    cancelled_rides = int(_status_matches(ride_df["Ride_Status"], "cancelled").sum())
    completion_rate = round(completed_rides / total_rides * 100, 1) if total_rides else 0

    city_summary = (
        ride_df.groupby("City")["Fare_INR"]
        .sum()
        .sort_values(ascending=False)
        .head(6)
    )
    ride_type_summary = (
        ride_df.groupby("Ride_Type")["Fare_INR"]
        .sum()
        .sort_values(ascending=False)
    )
    payment_summary = ride_df["Payment_Method"].value_counts().head(5)
    driver_summary = (
        ride_df.dropna(subset=["Driver_ID"])
        .groupby("Driver_ID")["Ride_ID"]
        .count()
        .sort_values(ascending=False)
        .head(6)
    )
    status_categories = ride_df["Ride_Status"].map(_status_category)
    status_summary = (
        status_categories.value_counts()
        .reindex(["completed", "cancelled", "other"], fill_value=0)
    )
    monthly_trend = (
        ride_df.dropna(subset=["_date"])
        .assign(_month=lambda rows: rows["_date"].dt.to_period("M"))
        .groupby("_month")
        .agg(rides=("Ride_ID", "count"), revenue=("Fare_INR", "sum"))
        .sort_index()
    )

    return render_template(
        "ride_analysis.html",
        total_rides=total_rides,
        total_revenue=round(total_revenue, 2),
        average_fare=round(average_fare, 2),
        average_rating=average_rating,
        completed_rides=completed_rides,
        cancelled_rides=cancelled_rides,
        completion_rate=completion_rate,
        city_count=ride_df["City"].nunique(),
        city_labels=city_summary.index.tolist(),
        city_values=city_summary.round(2).tolist(),
        ride_type_labels=ride_type_summary.index.tolist(),
        ride_type_values=ride_type_summary.round(2).tolist(),
        payment_labels=payment_summary.index.tolist(),
        payment_values=payment_summary.astype(int).tolist(),
        monthly_labels=[month.strftime("%b %Y") for month in monthly_trend.index],
        monthly_rides=monthly_trend["rides"].astype(int).tolist(),
        monthly_revenue=monthly_trend["revenue"].round(2).tolist(),
        status_labels=[status.title() for status in status_summary.index],
        status_values=status_summary.astype(int).tolist(),
        driver_labels=[str(driver) for driver in driver_summary.index],
        driver_values=driver_summary.astype(int).tolist(),
        years=years,
        months=months,
        ride_types=ride_types,
        ride_statuses=ride_statuses,
        month_names=[
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        selected_year=selected_year,
        selected_month=selected_month,
        selected_ride_type=selected_ride_type,
        selected_status=selected_status,
    )


@app.route("/bookings")
def bookings():
    redirect_response = _workspace_redirect("bookings")
    if redirect_response is not None:
        return redirect_response

    booking_df = df.copy()
    booking_df["_date"] = pd.to_datetime(booking_df["Date"], errors="coerce")
    records = (
        booking_df.sort_values("_date", ascending=False)
        .head(200)
        .assign(Date=lambda rides: rides["_date"].dt.strftime("%d %b %Y"))
        .to_dict("records")
    )
    return render_template(
        "bookings.html",
        bookings=records,
        total_bookings=len(booking_df),
        displayed_bookings=len(records),
    )


@app.route("/revenue")
def revenue():
    redirect_response = _workspace_redirect("revenue")
    if redirect_response is not None:
        return redirect_response

    revenue_df = df.copy()
    revenue_df["_date"] = pd.to_datetime(revenue_df["Date"], errors="coerce")
    monthly_revenue = (
        revenue_df.dropna(subset=["_date"])
        .assign(_month=lambda rides: rides["_date"].dt.to_period("M"))
        .groupby("_month")["Fare_INR"]
        .sum()
        .sort_index()
    )
    city_revenue = (
        revenue_df.groupby("City")["Fare_INR"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
    )
    ride_type_revenue = (
        revenue_df.groupby("Ride_Type")["Fare_INR"]
        .sum()
        .sort_values(ascending=False)
    )

    return render_template(
        "revenue.html",
        total_revenue=round(float(revenue_df["Fare_INR"].sum()), 2),
        average_fare=round(float(revenue_df["Fare_INR"].mean()), 2) if len(revenue_df) else 0,
        total_bookings=len(revenue_df),
        month_labels=[month.strftime("%b %Y") for month in monthly_revenue.index],
        monthly_revenue=monthly_revenue.round(2).tolist(),
        city_labels=city_revenue.index.tolist(),
        city_revenue=city_revenue.round(2).tolist(),
        ride_type_labels=ride_type_revenue.index.tolist(),
        ride_type_revenue=ride_type_revenue.round(2).tolist(),
    )


@app.route("/drivers")
def drivers():
    redirect_response = _workspace_redirect("drivers")
    if redirect_response is not None:
        return redirect_response

    driver_df = df.dropna(subset=["Driver_ID"]).copy()
    driver_stats = (
        driver_df.groupby("Driver_ID", as_index=False)
        .agg(
            bookings=("Ride_ID", "count"),
            revenue=("Fare_INR", "sum"),
            average_rating=("Customer_Rating", "mean"),
        )
    )
    latest_driver_ride = (
        driver_df.assign(_date=pd.to_datetime(driver_df["Date"], errors="coerce"))
        .sort_values("_date", ascending=False)
        .drop_duplicates("Driver_ID")
        [["Driver_ID", "City", "Ride_Status", "_date"]]
    )
    driver_summary = (
        driver_stats.merge(latest_driver_ride, on="Driver_ID", how="left")
        .sort_values(["bookings", "revenue"], ascending=[False, False])
        .head(100)
        .assign(
            average_rating=lambda rows: rows["average_rating"].round(1),
            Ride_Status=lambda rows: rows["Ride_Status"].str.replace("_", " ").str.title(),
            last_seen=lambda rows: rows["_date"].dt.strftime("%d %b %Y"),
        )
        .fillna({"average_rating": 0, "City": "Unknown", "Ride_Status": "Unknown", "last_seen": "Unknown"})
        .to_dict("records")
    )

    return render_template(
        "drivers.html",
        drivers=driver_summary,
        total_drivers=int(driver_df["Driver_ID"].nunique()),
        displayed_drivers=len(driver_summary),
        total_bookings=len(driver_df),
    )


@app.route("/city-analysis")
def city_analysis():
    redirect_response = _workspace_redirect("city")
    if redirect_response is not None:
        return redirect_response

    city_df = df.copy()
    city_df["_date"] = pd.to_datetime(city_df["Date"], errors="coerce")
    years = sorted(city_df["_date"].dropna().dt.year.unique().astype(int).tolist(), reverse=True)
    months = list(range(1, 13))
    ride_types = sorted(city_df["Ride_Type"].dropna().astype(str).unique().tolist())
    ride_statuses = sorted(city_df["Ride_Status"].dropna().astype(str).unique().tolist())

    selected_year = request.args.get("year", "all")
    if selected_year != "all" and selected_year not in {str(year) for year in years}:
        selected_year = "all"
    selected_month = request.args.get("month", "all")
    if selected_month != "all" and selected_month not in {str(month) for month in months}:
        selected_month = "all"
    selected_ride_type = request.args.get("ride_type", "all")
    if selected_ride_type not in ride_types:
        selected_ride_type = "all"
    selected_status = request.args.get("status", "all")
    if selected_status not in ride_statuses:
        selected_status = "all"

    if selected_year != "all":
        city_df = city_df[city_df["_date"].dt.year == int(selected_year)]
    if selected_month != "all":
        city_df = city_df[city_df["_date"].dt.month == int(selected_month)]
    if selected_ride_type != "all":
        city_df = city_df[city_df["Ride_Type"].astype(str) == selected_ride_type]
    if selected_status != "all":
        city_df = city_df[city_df["Ride_Status"].astype(str) == selected_status]

    city_summary = (
        city_df.groupby("City")
        .agg(rides=("Ride_ID", "count"), revenue=("Fare_INR", "sum"))
        .sort_values(["rides", "revenue"], ascending=[False, False])
    )
    total_rides = len(city_df)
    total_cities = int(city_df["City"].nunique())
    top_city_name = str(city_summary.index[0]) if not city_summary.empty else "N/A"
    top_city_rides = int(city_summary.iloc[0]["rides"]) if not city_summary.empty else 0
    average_rides_per_city = round(total_rides / total_cities) if total_cities else 0

    city_share = city_summary["rides"].head(5).astype(int)
    if len(city_summary) > 5:
        city_share.loc["Other"] = int(city_summary["rides"].iloc[5:].sum())

    region_by_city = {
        "Ahmedabad": "West",
        "Mumbai": "West",
        "Pune": "West",
        "Delhi": "North",
        "Kolkata": "East",
        "Bengaluru": "South",
        "Chennai": "South",
        "Hyderabad": "South",
    }
    region_summary = (
        city_df.assign(Region=city_df["City"].map(region_by_city).fillna("Other"))
        .groupby("Region")["Ride_ID"]
        .count()
        .sort_values(ascending=False)
    )
    monthly_city_rides = (
        city_df.dropna(subset=["_date"])
        .assign(_month=lambda rides: rides["_date"].dt.to_period("M"))
        .groupby("_month")["Ride_ID"]
        .count()
        .sort_index()
    )
    if monthly_city_rides.empty:
        month_labels = []
        monthly_bookings = []
    else:
        recent_months = pd.period_range(
            end=monthly_city_rides.index.max(), periods=6, freq="M"
        )
        monthly_city_rides = monthly_city_rides.reindex(recent_months, fill_value=0)
        month_labels = [month.strftime("%b") for month in recent_months]
        monthly_bookings = monthly_city_rides.astype(int).tolist()

    top_cities = (
        city_summary.sort_values(["revenue", "rides"], ascending=[False, False])
        .head(6)
        .reset_index()
        .to_dict("records")
    )
    valid_dates = city_df["_date"].dropna()
    period_label = (
        f"{valid_dates.min():%b %Y} – {valid_dates.max():%b %Y}"
        if not valid_dates.empty
        else "No dates in selection"
    )

    return render_template(
        "city_analysis.html",
        total_rides=total_rides,
        total_cities=total_cities,
        top_city_name=top_city_name,
        top_city_rides=top_city_rides,
        average_rides_per_city=average_rides_per_city,
        total_revenue=round(float(city_df["Fare_INR"].sum()), 2),
        city_labels=city_summary.index.tolist(),
        city_values=city_summary["rides"].astype(int).tolist(),
        city_colors=["#9057f4", "#26bf87", "#3789ef", "#ffa23f", "#e95297", "#50b6ca"],
        city_share_labels=city_share.index.tolist(),
        city_share_values=city_share.tolist(),
        region_labels=region_summary.index.tolist(),
        region_values=region_summary.astype(int).tolist(),
        booking_months=month_labels,
        monthly_bookings=monthly_bookings,
        top_cities=top_cities,
        period_label=period_label,
        years=years,
        months=months,
        ride_types=ride_types,
        ride_statuses=ride_statuses,
        month_names=[
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        selected_year=selected_year,
        selected_month=selected_month,
        selected_ride_type=selected_ride_type,
        selected_status=selected_status,
    )


@app.route("/payment-analysis")
def payment_analysis():
    redirect_response = _workspace_redirect("payment")
    if redirect_response is not None:
        return redirect_response

    payment_df = df.copy()
    payment_df["_date"] = pd.to_datetime(payment_df["Date"], errors="coerce")
    years = sorted(payment_df["_date"].dropna().dt.year.unique().astype(int).tolist(), reverse=True)
    months = list(range(1, 13))
    payment_methods = sorted(payment_df["Payment_Method"].dropna().astype(str).unique().tolist())
    ride_statuses = sorted(payment_df["Ride_Status"].dropna().astype(str).unique().tolist())

    selected_year = request.args.get("year", "all")
    if selected_year != "all" and selected_year not in {str(year) for year in years}:
        selected_year = "all"
    selected_month = request.args.get("month", "all")
    if selected_month != "all" and selected_month not in {str(month) for month in months}:
        selected_month = "all"
    selected_method = request.args.get("method", "all")
    if selected_method not in payment_methods:
        selected_method = "all"
    selected_status = request.args.get("status", "all")
    if selected_status not in ride_statuses:
        selected_status = "all"

    if selected_year != "all":
        payment_df = payment_df[payment_df["_date"].dt.year == int(selected_year)]
    if selected_month != "all":
        payment_df = payment_df[payment_df["_date"].dt.month == int(selected_month)]
    if selected_method != "all":
        payment_df = payment_df[payment_df["Payment_Method"].astype(str) == selected_method]
    if selected_status != "all":
        payment_df = payment_df[payment_df["Ride_Status"].astype(str) == selected_status]

    payment_summary = (
        payment_df.groupby("Payment_Method")
        .agg(rides=("Ride_ID", "count"), revenue=("Fare_INR", "sum"))
        .sort_values("rides", ascending=False)
    )
    payment_methods_in_selection = payment_summary.index.tolist()
    total_payments = len(payment_df)
    total_revenue = float(payment_df["Fare_INR"].sum())
    payment_method_lower = payment_df["Payment_Method"].fillna("").astype(str).str.strip().str.lower()
    cash_mask = payment_method_lower == "cash"
    online_mask = payment_method_lower.isin(["upi", "credit card", "debit card", "uber wallet"])
    cash_revenue = float(payment_df.loc[cash_mask, "Fare_INR"].sum())
    online_revenue = float(payment_df.loc[online_mask, "Fare_INR"].sum())
    payment_share = payment_summary["rides"]

    valid_payment_dates = payment_df.dropna(subset=["_date"])
    if valid_payment_dates.empty:
        month_labels = []
        monthly_payment_revenue = {method: [] for method in payment_methods_in_selection}
        period_label = "No dates in selection"
    else:
        monthly_source = (
            valid_payment_dates.assign(_month=lambda rows: rows["_date"].dt.to_period("M"))
            .groupby(["_month", "Payment_Method"])["Fare_INR"]
            .sum()
            .sort_index()
        )
        recent_months = pd.period_range(end=valid_payment_dates["_date"].max().to_period("M"), periods=6, freq="M")
        month_labels = [month.strftime("%b") for month in recent_months]
        monthly_payment_revenue = {}
        for method in payment_methods_in_selection:
            method_monthly = monthly_source.xs(method, level="Payment_Method") if method in monthly_source.index.get_level_values("Payment_Method") else pd.Series(dtype=float)
            monthly_payment_revenue[method] = [
                round(float(method_monthly.get(month, 0)), 2) for month in recent_months
            ]
        period_label = (
            f"{valid_payment_dates['_date'].min():%b %Y} – {valid_payment_dates['_date'].max():%b %Y}"
        )

    recent_transaction_frame = (
        payment_df.sort_values("_date", ascending=False)
        .head(8)
        .assign(display_date=lambda rides: rides["_date"].dt.strftime("%d %b %Y"))
    )
    recent_transactions = recent_transaction_frame[
        ["Ride_ID", "display_date", "Payment_Method", "Fare_INR", "Ride_Status"]
    ].to_dict("records")
    payment_chart_data = {
        "methodLabels": payment_methods_in_selection,
        "methodRideCounts": payment_summary["rides"].astype(int).tolist(),
        "methodRevenue": payment_summary["revenue"].round(2).tolist(),
        "shareLabels": [method.title() for method in payment_share.index],
        "shareValues": payment_share.astype(int).tolist(),
        "monthLabels": month_labels,
        "monthRevenue": monthly_payment_revenue,
    }

    return render_template(
        "payment_analysis.html",
        recent_transactions=recent_transactions,
        payment_chart_data=payment_chart_data,
        total_payments=total_payments,
        total_revenue=round(total_revenue, 2),
        cash_revenue=round(cash_revenue, 2),
        online_revenue=round(online_revenue, 2),
        period_label=period_label,
        years=years,
        months=months,
        payment_methods=payment_methods,
        ride_statuses=ride_statuses,
        month_names=[
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        selected_year=selected_year,
        selected_month=selected_month,
        selected_method=selected_method,
        selected_status=selected_status,
    )


@app.route("/ride-status")
def ride_status():
    redirect_response = _workspace_redirect("ride-status")
    if redirect_response is not None:
        return redirect_response

    status_df = df.copy()
    status_df["_date"] = pd.to_datetime(status_df["Date"], errors="coerce")
    status_df["_status_category"] = status_df["Ride_Status"].map(_status_category)
    years = sorted(
        status_df["_date"].dropna().dt.year.unique().astype(int).tolist(),
        reverse=True,
    )
    months = list(range(1, 13))
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    cities = sorted(status_df["City"].dropna().astype(str).unique().tolist())
    ride_types = sorted(status_df["Ride_Type"].dropna().astype(str).unique().tolist())

    selected_year = request.args.get("year", "all")
    if selected_year != "all" and selected_year not in {str(year) for year in years}:
        selected_year = "all"
    selected_month = request.args.get("month", "all")
    if selected_month != "all" and selected_month not in {str(month) for month in months}:
        selected_month = "all"
    selected_city = request.args.get("city", "all")
    if selected_city not in cities:
        selected_city = "all"
    selected_type = request.args.get("type", "all")
    if selected_type not in ride_types:
        selected_type = "all"

    if selected_year != "all":
        status_df = status_df[status_df["_date"].dt.year == int(selected_year)]
    if selected_month != "all":
        status_df = status_df[status_df["_date"].dt.month == int(selected_month)]
    if selected_city != "all":
        status_df = status_df[status_df["City"].astype(str) == selected_city]
    if selected_type != "all":
        status_df = status_df[status_df["Ride_Type"].astype(str) == selected_type]

    status_counts = status_df["Ride_Status"].value_counts().sort_values(ascending=False)
    completed_count = int((status_df["_status_category"] == "completed").sum())
    cancelled_count = int((status_df["_status_category"] == "cancelled").sum())
    total_rides = len(status_df)

    chart_months = (
        [int(selected_month)]
        if selected_month != "all"
        else months
    )
    monthly_total = (
        status_df.dropna(subset=["_date"])
        .assign(_month=lambda rides: rides["_date"].dt.month)
        .groupby("_month")
        .size()
    )
    monthly_by_category = (
        status_df.dropna(subset=["_date"])
        .assign(_month=lambda rides: rides["_date"].dt.month)
        .groupby(["_month", "_status_category"])
        .size()
        .unstack(fill_value=0)
    )
    month_labels = [month_names[month - 1][:3] for month in chart_months]
    monthly_rides = [
        int(monthly_total.get(month, 0)) for month in chart_months
    ]
    monthly_completed = [
        int(monthly_by_category.loc[month, "completed"])
        if month in monthly_by_category.index and "completed" in monthly_by_category.columns
        else 0
        for month in chart_months
    ]
    monthly_cancelled = [
        int(monthly_by_category.loc[month, "cancelled"])
        if month in monthly_by_category.index and "cancelled" in monthly_by_category.columns
        else 0
        for month in chart_months
    ]

    cancelled_df = status_df[status_df["_status_category"] == "cancelled"]
    cancellation_reasons = (
        cancelled_df["Cancellation_Reason"]
        .fillna("Not recorded")
        .astype(str)
        .str.strip()
        .replace("", "Not recorded")
        .value_counts()
        .head(5)
    )

    city_counts = status_df["City"].value_counts()
    top_city_counts = city_counts.head(5)
    max_top_city_rides = int(top_city_counts.max()) if not top_city_counts.empty else 0
    top_city_rows = [
        {
            "name": city,
            "rides": int(count),
            "share": count / total_rides * 100 if total_rides else 0,
            "bar_width": count / max_top_city_rides * 100 if max_top_city_rides else 0,
        }
        for city, count in top_city_counts.items()
    ]
    city_coordinates = {
        "Ahmedabad": (23.0225, 72.5714),
        "Bengaluru": (12.9716, 77.5946),
        "Chennai": (13.0827, 80.2707),
        "Delhi": (28.6139, 77.2090),
        "Hyderabad": (17.3850, 78.4867),
        "Kolkata": (22.5726, 88.3639),
        "Mumbai": (19.0760, 72.8777),
        "Pune": (18.5204, 73.8567),
    }
    map_bounds = {"min_lon": 67.0, "max_lon": 98.0, "min_lat": 6.5, "max_lat": 37.5}

    def project_to_india_map(latitude, longitude):
        x_percent = (longitude - map_bounds["min_lon"]) / (
            map_bounds["max_lon"] - map_bounds["min_lon"]
        ) * 100
        y_percent = (map_bounds["max_lat"] - latitude) / (
            map_bounds["max_lat"] - map_bounds["min_lat"]
        ) * 100
        return round(x_percent, 2), round(y_percent, 2)

    outline_coordinates = [
        (74.5, 37.0), (77.0, 35.5), (79.5, 34.0), (80.8, 31.5),
        (83.5, 30.0), (85.5, 28.5), (88.5, 27.8), (90.0, 26.5),
        (91.0, 26.2), (92.0, 27.2), (94.4, 28.2), (96.5, 28.0),
        (97.5, 26.2), (96.5, 24.5), (94.2, 23.8), (92.2, 22.5),
        (90.5, 21.3), (89.0, 22.0), (87.5, 21.5), (86.5, 20.0),
        (85.0, 18.0), (84.0, 16.0), (82.5, 14.0), (81.0, 11.5),
        (79.2, 8.3), (77.5, 7.2), (76.4, 9.0), (75.0, 11.5),
        (74.0, 14.0), (72.8, 16.8), (71.0, 18.8), (69.5, 20.5),
        (68.0, 22.2), (69.5, 24.0), (72.0, 25.5), (73.0, 28.0),
        (74.0, 31.0), (73.5, 34.0),
    ]

    def outline_point(longitude, latitude):
        x = (longitude - map_bounds["min_lon"]) / (
            map_bounds["max_lon"] - map_bounds["min_lon"]
        ) * 400
        y = (map_bounds["max_lat"] - latitude) / (
            map_bounds["max_lat"] - map_bounds["min_lat"]
        ) * 360
        return f"{x:.1f},{y:.1f}"

    india_outline = " ".join(
        outline_point(longitude, latitude)
        for longitude, latitude in outline_coordinates
    )
    max_city_rides = int(city_counts.max()) if not city_counts.empty else 0
    city_heatmap = []
    for city, count in city_counts.items():
        if city not in city_coordinates:
            continue
        latitude, longitude = city_coordinates[city]
        x_percent, y_percent = project_to_india_map(latitude, longitude)
        city_heatmap.append(
            {
                "name": city,
                "rides": int(count),
                "x": x_percent,
                "y": y_percent,
                "intensity": round(count / max_city_rides, 2) if max_city_rides else 0,
            }
        )

    top_ride_types = status_df["Ride_Type"].value_counts().head(4).index.tolist()
    ride_type_status = (
        status_df[status_df["Ride_Type"].isin(top_ride_types)]
        .groupby(["Ride_Type", "_status_category"])
        .size()
        .unstack(fill_value=0)
    )
    recent_rides = (
        status_df.sort_values("_date", ascending=False)
        .head(8)
        .assign(
            display_date=lambda rides: rides["_date"].dt.strftime("%b %d, %Y"),
        )
        [["Ride_ID", "display_date", "City", "Ride_Type", "Ride_Status", "Fare_INR"]]
        .fillna({"display_date": "Unknown", "City": "Unknown", "Ride_Type": "Unknown"})
        .to_dict("records")
    )
    valid_dates = status_df["_date"].dropna()
    completion_rate = round(completed_count / total_rides * 100, 1) if total_rides else 0
    payment_counts = status_df["Payment_Method"].value_counts()
    ride_status_chart_data = {
        "statusLabels": [status.replace("_", " ").title() for status in status_counts.index],
        "statusValues": status_counts.astype(int).tolist(),
        "monthLabels": month_labels,
        "monthlyRides": monthly_rides,
        "monthlyCompleted": monthly_completed,
        "monthlyCancelled": monthly_cancelled,
        "cancellationLabels": cancellation_reasons.index.tolist(),
        "cancellationValues": cancellation_reasons.astype(int).tolist(),
        "rideTypeLabels": top_ride_types,
        "rideTypeCompleted": (
            ride_type_status["completed"].reindex(top_ride_types, fill_value=0).astype(int).tolist()
            if "completed" in ride_type_status.columns
            else [0] * len(top_ride_types)
        ),
        "rideTypeCancelled": (
            ride_type_status["cancelled"].reindex(top_ride_types, fill_value=0).astype(int).tolist()
            if "cancelled" in ride_type_status.columns
            else [0] * len(top_ride_types)
        ),
        "rideTypeOther": (
            ride_type_status["other"].reindex(top_ride_types, fill_value=0).astype(int).tolist()
            if "other" in ride_type_status.columns
            else [0] * len(top_ride_types)
        ),
        "paymentLabels": payment_counts.index.tolist(),
        "paymentValues": payment_counts.astype(int).tolist(),
        "completionRate": completion_rate,
    }

    return render_template(
        "ride_status.html",
        total_rides=total_rides,
        completed_count=completed_count,
        cancelled_count=cancelled_count,
        completion_rate=completion_rate,
        period_start=valid_dates.min().strftime("%d %b %Y") if not valid_dates.empty else "N/A",
        period_end=valid_dates.max().strftime("%d %b %Y") if not valid_dates.empty else "N/A",
        city_heatmap=city_heatmap,
        india_outline=india_outline,
        top_city_rows=top_city_rows,
        ride_status_chart_data=ride_status_chart_data,
        recent_rides=recent_rides,
        years=years,
        months=months,
        month_names=month_names,
        cities=cities,
        ride_types=ride_types,
        selected_year=selected_year,
        selected_month=selected_month,
        selected_city=selected_city,
        selected_type=selected_type,
    )


@app.route("/live-team")
def live_team():
    redirect_response = _workspace_redirect("live-team")
    if redirect_response is not None:
        return redirect_response

    team_df = df.copy()
    team_df["_date"] = pd.to_datetime(team_df["Date"], errors="coerce")
    team_members = (
        team_df.dropna(subset=["Driver_ID"])
        .sort_values("_date", ascending=False)
        .drop_duplicates("Driver_ID")
        .head(8)
        .assign(
            Date=lambda rides: rides["_date"].dt.strftime("%d %b %Y"),
            Status_Category=lambda rides: rides["Ride_Status"].map(
                lambda value: "completed"
                if value.startswith("completed")
                else "cancelled"
                if value.startswith("cancelled")
                else "other"
            ),
            Ride_Status=lambda rides: rides["Ride_Status"].str.replace("_", " ").str.title(),
        )
        .to_dict("records")
    )

    return render_template(
        "live_team.html",
        total_drivers=int(team_df["Driver_ID"].nunique()),
        cities_covered=int(team_df["City"].nunique()),
        latest_date=team_df["_date"].max().strftime("%d %b %Y")
        if team_df["_date"].notna().any()
        else "No date available",
        team_members=team_members,
    )


@app.route("/reports")
def reports():
    redirect_response = _workspace_redirect("reports")
    if redirect_response is not None:
        return redirect_response

    report_df = df.copy()
    report_df["_date"] = pd.to_datetime(report_df["Date"], errors="coerce")
    years = sorted(
        report_df["_date"].dropna().dt.year.unique().astype(int).tolist(),
        reverse=True,
    )
    cities = sorted(report_df["City"].dropna().astype(str).unique().tolist())
    ride_types = sorted(report_df["Ride_Type"].dropna().astype(str).unique().tolist())
    months = list(range(1, 13))

    selected_year = request.args.get("year", "all")
    if selected_year != "all" and selected_year not in {str(year) for year in years}:
        selected_year = "all"
    selected_month = request.args.get("month", "all")
    if selected_month != "all" and selected_month not in {str(month) for month in months}:
        selected_month = "all"
    selected_city = request.args.get("city", "all")
    if selected_city not in cities:
        selected_city = "all"
    selected_type = request.args.get("type", "all")
    if selected_type not in ride_types:
        selected_type = "all"

    if selected_year != "all":
        report_df = report_df[report_df["_date"].dt.year == int(selected_year)]
    if selected_month != "all":
        report_df = report_df[report_df["_date"].dt.month == int(selected_month)]
    if selected_city != "all":
        report_df = report_df[report_df["City"].astype(str) == selected_city]
    if selected_type != "all":
        report_df = report_df[report_df["Ride_Type"].astype(str) == selected_type]

    total_rides = len(report_df)
    total_revenue = float(report_df["Fare_INR"].sum())
    average_rating = (
        float(report_df["Customer_Rating"].mean())
        if report_df["Customer_Rating"].notna().any()
        else 0
    )
    completed_rides = int(_status_matches(report_df["Ride_Status"], "completed").sum())
    cancelled_rides = int(_status_matches(report_df["Ride_Status"], "cancelled").sum())
    customer_cancellations = int(
        _status_matches(report_df["Ride_Status"], "cancelled by customer").sum()
    )
    driver_cancellations = int(
        _status_matches(report_df["Ride_Status"], "cancelled by driver").sum()
    )

    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    chart_months = (
        [int(selected_month)]
        if selected_month != "all"
        else months
    )
    monthly_report = (
        report_df.dropna(subset=["_date"])
        .assign(_month=lambda rides: rides["_date"].dt.month)
        .groupby("_month")
        .agg(rides=("Ride_ID", "count"), revenue=("Fare_INR", "sum"))
    )
    month_labels = [month_names[month - 1][:3] for month in chart_months]
    monthly_rides = [
        int(monthly_report.loc[month, "rides"]) if month in monthly_report.index else 0
        for month in chart_months
    ]
    monthly_revenue = [
        round(float(monthly_report.loc[month, "revenue"]), 2)
        if month in monthly_report.index
        else 0
        for month in chart_months
    ]

    city_report = (
        report_df.groupby("City", as_index=False)
        .agg(
            rides=("Ride_ID", "count"),
            revenue=("Fare_INR", "sum"),
            average_fare=("Fare_INR", "mean"),
        )
        .sort_values(["rides", "revenue"], ascending=[False, False])
        .head(5)
        .fillna(0)
    )
    city_report["share"] = (
        city_report["rides"] / total_rides * 100 if total_rides else 0
    )
    top_city_rides = int(city_report["rides"].max()) if not city_report.empty else 0
    city_report["bar_width"] = (
        city_report["rides"] / top_city_rides * 100 if top_city_rides else 0
    )

    ride_type_report = report_df["Ride_Type"].value_counts().sort_values(ascending=False)
    payment_report = report_df["Payment_Method"].value_counts().sort_values(ascending=False)
    rating_groups = pd.Series(
        {
            "5 stars": int((report_df["Customer_Rating"] >= 4.5).sum()),
            "4 stars": int(
                ((report_df["Customer_Rating"] >= 3.5) & (report_df["Customer_Rating"] < 4.5)).sum()
            ),
            "3 stars": int((report_df["Customer_Rating"] < 3.5).sum()),
        }
    )
    cancellation_report = (
        report_df.loc[
            _status_matches(report_df["Ride_Status"], "cancelled"),
            "Cancellation_Reason",
        ]
        .fillna("Not recorded")
        .value_counts()
        .sort_values(ascending=False)
    )
    valid_dates = report_df["_date"].dropna()

    return render_template(
        "reports.html",
        total_rides=total_rides,
        total_revenue=round(total_revenue, 2),
        average_rating=round(average_rating, 1),
        rated_rides=int(report_df["Customer_Rating"].notna().sum()),
        completion_rate=round(completed_rides / total_rides * 100, 1) if total_rides else 0,
        completed_rides=completed_rides,
        cancelled_rides=cancelled_rides,
        customer_cancellations=customer_cancellations,
        driver_cancellations=driver_cancellations,
        period_start=valid_dates.min().strftime("%d %b %Y") if not valid_dates.empty else "N/A",
        period_end=valid_dates.max().strftime("%d %b %Y") if not valid_dates.empty else "N/A",
        city_report=city_report.to_dict("records"),
        years=years,
        months=months,
        month_names=month_names,
        cities=cities,
        ride_types=ride_types,
        selected_year=selected_year,
        selected_month=selected_month,
        selected_city=selected_city,
        selected_type=selected_type,
        report_chart_data={
            "monthLabels": month_labels,
            "monthlyRides": monthly_rides,
            "monthlyRevenue": monthly_revenue,
            "rideTypeLabels": ride_type_report.index.tolist(),
            "rideTypeValues": ride_type_report.astype(int).tolist(),
            "ratingLabels": rating_groups.index.tolist(),
            "ratingValues": rating_groups.astype(int).tolist(),
            "cancellationLabels": cancellation_report.index.tolist(),
            "cancellationValues": cancellation_report.astype(int).tolist(),
            "paymentLabels": payment_report.index.tolist(),
            "paymentValues": payment_report.astype(int).tolist(),
        },
    )


if __name__ == "__main__":
    print("Uber Dataset Loaded Successfully!")
    print("Total Records:", len(df))
    app.run(debug=True)
