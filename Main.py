from flask import Flask, render_template_string, request, session, redirect, url_for
import random

app = Flask(__name__)
app.secret_key = "shop-economy-dev-secret"
MAX_DAYS = 30

# =========================
# ТОВАРЫ (сложный уровень — несколько товаров сразу)
# =========================

PRODUCTS_HARD = {
    "headphones": {"name": "🎧 Наушники", "cost": 800, "competitor_price": 1500},
    "cases": {"name": "📱 Чехлы для телефона", "cost": 300, "competitor_price": 600},
    "chargers": {"name": "🔌 Зарядные устройства", "cost": 500, "competitor_price": 1000},
}

# =========================
# СЛУЧАЙНЫЕ СОБЫТИЯ
# =========================

EVENTS = [
    {
        "key": "blogger",
        "text": "Популярный блогер рассказал о товаре «{product}». Спрос +30%",
        "demand_mult": 1.3,
    },
    {
        "key": "supply_up",
        "text": "Поставщик повысил цену на «{product}». Себестоимость +15%",
        "cost_mult": 1.15,
    },
    {
        "key": "new_competitor",
        "text": "В городе открылся новый магазин с товаром «{product}». Спрос −15%",
        "demand_mult": 0.85,
    },
    {
        "key": "trend",
        "text": "Товар «{product}» внезапно стал популярным. Спрос ×2",
        "demand_mult": 2.0,
    },
    {
        "key": "crisis",
        "text": "Доходы населения снизились. Спрос на «{product}» −25%",
        "demand_mult": 0.75,
    },
]

EVENT_PROBABILITY = 0.35  # шанс, что за день произойдёт случайное событие


def roll_event(product_names):
    if random.random() < EVENT_PROBABILITY:
        template = random.choice(EVENTS)
        product = random.choice(product_names)
        text = "📢 " + template["text"].format(product=product)
        return {
            "product": product,
            "text": text,
            "demand_mult": template.get("demand_mult", 1.0),
            "cost_mult": template.get("cost_mult"),
        }
    return None


# =========================
# УРОВНИ СЛОЖНОСТИ
# =========================

LEVELS = {
    "easy": {
        "title": "🟢 Лёгкий — только цена",
        "desc": "Вы управляете только ценой. Закупка происходит автоматически, рекламы и налогов нет.",
    },
    "medium": {
        "title": "🟡 Средний — цена, закупки, реклама",
        "desc": "Вы управляете ценой, закупками, рекламой и запасами одного товара. Есть аренда и зарплата.",
    },
    "hard": {
        "title": "🔴 Сложный — полная экономика, 3 товара",
        "desc": "Три товара сразу, налоги, инфляция, колебания цен конкурентов и возможность взять кредит.",
    },
}

EASY_STOCK_TARGET = 60


# =========================
# ИГРОВАЯ ЛОГИКА
# =========================

def new_game(level):
    game = {
        "level": level,
        "day": 1,
        "balance": 100000,
        "reputation": 50,
        "profit": 0,
        "total_sold": 0,
        "total_revenue": 0,
        "total_expenses": 0,
        "credit_debt": 0,
    }

    if level == "hard":
        game["products"] = {
            pid: {
                "name": info["name"],
                "stock": 100,
                "cost": info["cost"],
                "competitor_price": info["competitor_price"],
                "last_price": None,
                "last_demand": None,
            }
            for pid, info in PRODUCTS_HARD.items()
        }
    else:
        game["stock"] = 100
        game["cost"] = 800
        game["competitor_price"] = 1500
        game["last_price"] = None
        game["last_demand"] = None

    return game


def calculate_demand(price, competitor_price, ad_factor=1.0, reputation=50, event_mult=1.0):
    base_demand = 60
    elasticity = 1.5

    price = max(price, 1)
    price_factor = (competitor_price / price) ** elasticity
    random_factor = random.uniform(0.9, 1.1)
    reputation_factor = 1 + (reputation - 50) * 0.004

    demand = (
        base_demand
        * price_factor
        * random_factor
        * ad_factor
        * reputation_factor
        * event_mult
    )

    return max(0, round(demand))


def update_reputation(game, overpriced_count, competitive_count, any_stockout):
    delta = competitive_count - overpriced_count * 2
    delta += -3 if any_stockout else 1
    game["reputation"] = min(100, max(0, game["reputation"] + delta))


def generate_conclusion(prev_price, price, prev_demand, demand, profit, label=""):
    parts = []
    prefix = f"{label}: " if label else ""

    if prev_price and prev_price > 0 and prev_demand and prev_demand > 0:
        price_change_pct = (price - prev_price) / prev_price * 100
        demand_change_pct = (demand - prev_demand) / prev_demand * 100

        if price_change_pct >= 1 and demand_change_pct < 0:
            parts.append(
                f"{prefix}цену подняли на {price_change_pct:.0f}%, спрос упал "
                f"на {abs(demand_change_pct):.0f}%."
            )
        elif price_change_pct <= -1 and demand_change_pct > 0:
            parts.append(
                f"{prefix}цену снизили на {abs(price_change_pct):.0f}%, спрос вырос "
                f"на {demand_change_pct:.0f}%."
            )

    if not parts:
        return ""

    return " ".join(parts)


def final_rating(profit_total, reputation):
    if profit_total >= 200000:
        stars = 5
    elif profit_total >= 100000:
        stars = 4
    elif profit_total >= 50000:
        stars = 3
    elif profit_total > 0:
        stars = 2
    else:
        stars = 1

    if profit_total >= 100000 and reputation >= 70:
        player_type = "💼 Успешный предприниматель"
    elif profit_total >= 100000 and reputation < 50:
        player_type = "🦈 Циничный делец"
    elif profit_total < 50000 and reputation >= 70:
        player_type = "🤝 Народный любимец"
    else:
        player_type = "📈 Начинающий бизнесмен"

    return stars, player_type


def process_day_single(game, form):
    """Лёгкий и средний уровни — один товар."""
    level = game["level"]

    try:
        price = float(form["price"])
    except (KeyError, ValueError):
        return None, "❌ Введите корректную цену."

    if price <= 0:
        return None, "❌ Цена должна быть больше нуля."

    if level == "easy":
        purchase = max(0, EASY_STOCK_TARGET - game["stock"])
        advertising = 0.0
    else:
        try:
            purchase = int(form.get("purchase", 0))
            advertising = float(form.get("advertising", 0))
        except ValueError:
            return None, "❌ Закупка и реклама должны быть числами."

        if purchase < 0:
            return None, "❌ Закупка не может быть отрицательной."
        if advertising < 0:
            return None, "❌ Бюджет на рекламу не может быть отрицательным."

    purchase_cost = purchase * game["cost"]

    if purchase_cost + advertising > game["balance"]:
        return None, "❌ Недостаточно денег на закупку и рекламу!"

    event = roll_event([("наушники")])
    event_text = event["text"] if event else ""
    demand_event_mult = event["demand_mult"] if event else 1.0
    if event and event["cost_mult"]:
        game["cost"] = round(game["cost"] * event["cost_mult"], 2)

    game["stock"] += purchase
    game["balance"] -= purchase_cost

    ad_factor = 1 + min(advertising / 10000, 0.3) if level != "easy" else 1.0

    demand = calculate_demand(
        price,
        game["competitor_price"],
        ad_factor=ad_factor,
        reputation=game["reputation"],
        event_mult=demand_event_mult,
    )

    sold = min(demand, game["stock"])
    stockout = demand > game["stock"]

    revenue = sold * price

    if level == "easy":
        rent, salary = 0, 0
    else:
        rent, salary = 2000, 3000

    expenses = purchase_cost + advertising + rent + salary
    profit = revenue - expenses

    game["balance"] += revenue
    game["stock"] -= sold
    game["profit"] += profit
    game["total_sold"] += sold
    game["total_revenue"] += revenue
    game["total_expenses"] += expenses

    update_reputation(
        game,
        overpriced_count=1 if price > game["competitor_price"] * 1.2 else 0,
        competitive_count=1 if price <= game["competitor_price"] else 0,
        any_stockout=stockout,
    )

    conclusion = generate_conclusion(
        game["last_price"], price, game["last_demand"], demand, profit
    )

    day_rentabelnost = (profit / expenses * 100) if expenses > 0 else 0

    result = {
        "mode": "single",
        "demand": demand,
        "sold": sold,
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "tax": 0,
        "interest": 0,
        "stockout": stockout,
        "event_text": event_text,
        "conclusion": conclusion,
        "rentabelnost": day_rentabelnost,
    }

    game["last_price"] = price
    game["last_demand"] = demand
    game["day"] += 1

    return result, None


def process_day_hard(game, form):
    """Сложный уровень — несколько товаров сразу."""
    products = game["products"]

    # --- Сбор и валидация ввода по каждому товару ---
    inputs = {}
    for pid, p in products.items():
        try:
            price = float(form[f"price_{pid}"])
            purchase = int(form.get(f"purchase_{pid}", 0))
        except (KeyError, ValueError):
            return None, f"❌ Проверьте цену и закупку для товара «{p['name']}»."

        if price <= 0:
            return None, f"❌ Цена товара «{p['name']}» должна быть больше нуля."
        if purchase < 0:
            return None, f"❌ Закупка товара «{p['name']}» не может быть отрицательной."

        inputs[pid] = {"price": price, "purchase": purchase}

    try:
        advertising = float(form.get("advertising", 0))
    except ValueError:
        return None, "❌ Бюджет на рекламу должен быть числом."
    if advertising < 0:
        return None, "❌ Бюджет на рекламу не может быть отрицательным."

    try:
        credit_taken = float(form.get("credit", 0))
    except ValueError:
        credit_taken = 0.0
    if credit_taken < 0:
        return None, "❌ Сумма кредита не может быть отрицательной."

    # --- Инфляция и колебания цен конкурентов ---
    for p in products.values():
        p["cost"] = round(p["cost"] * 1.005, 2)
        p["competitor_price"] = max(1, round(p["competitor_price"] * random.uniform(0.9, 1.1)))

    # --- Кредит и проценты ---
    if credit_taken > 0:
        game["balance"] += credit_taken
        game["credit_debt"] += credit_taken

    interest = 0.0
    if game["credit_debt"] > 0:
        interest = round(game["credit_debt"] * 0.05, 2)
        game["credit_debt"] += interest

    total_purchase_cost = sum(
        inputs[pid]["purchase"] * p["cost"] for pid, p in products.items()
    )

    if total_purchase_cost + advertising + interest > game["balance"]:
        return None, "❌ Недостаточно денег на закупку всех товаров, рекламу и кредит!"

    # --- Событие дня (затрагивает один случайный товар) ---
    product_names = [p["name"] for p in products.values()]
    event = roll_event(product_names)
    event_text = event["text"] if event else ""
    affected_pid = None
    if event:
        for pid, p in products.items():
            if p["name"] == event["product"]:
                affected_pid = pid
                break
        if event["cost_mult"] and affected_pid:
            products[affected_pid]["cost"] = round(
                products[affected_pid]["cost"] * event["cost_mult"], 2
            )

    ad_factor = 1 + min(advertising / 10000, 0.3)

    per_product_results = {}
    total_revenue = 0.0
    any_stockout = False
    overpriced_count = 0
    competitive_count = 0

    for pid, p in products.items():
        price = inputs[pid]["price"]
        purchase = inputs[pid]["purchase"]

        p["stock"] += purchase
        game["balance"] -= purchase * p["cost"]

        event_mult = event["demand_mult"] if (event and affected_pid == pid) else 1.0

        demand = calculate_demand(
            price,
            p["competitor_price"],
            ad_factor=ad_factor,
            reputation=game["reputation"],
            event_mult=event_mult,
        )

        sold = min(demand, p["stock"])
        stockout = demand > p["stock"]
        any_stockout = any_stockout or stockout

        revenue = sold * price
        total_revenue += revenue

        if price <= p["competitor_price"]:
            competitive_count += 1
        elif price > p["competitor_price"] * 1.2:
            overpriced_count += 1

        conclusion = generate_conclusion(
            p["last_price"], price, p["last_demand"], demand, revenue - purchase * p["cost"],
            label=p["name"],
        )

        per_product_results[pid] = {
            "name": p["name"],
            "price": price,
            "demand": demand,
            "sold": sold,
            "revenue": revenue,
            "stockout": stockout,
            "conclusion": conclusion,
        }

        p["last_price"] = price
        p["last_demand"] = demand
        p["stock"] -= sold

    rent, salary = 3000, 5000
    expenses = total_purchase_cost + advertising + rent + salary + interest
    profit = total_revenue - expenses

    tax = 0.0
    if profit > 0:
        tax = round(profit * 0.15, 2)
        profit -= tax
        expenses += tax

    game["balance"] += total_revenue
    game["balance"] -= tax
    game["profit"] += profit

    total_sold = sum(r["sold"] for r in per_product_results.values())
    game["total_sold"] += total_sold
    game["total_revenue"] += total_revenue
    game["total_expenses"] += expenses

    update_reputation(
        game,
        overpriced_count=overpriced_count,
        competitive_count=competitive_count,
        any_stockout=any_stockout,
    )

    conclusions = [r["conclusion"] for r in per_product_results.values() if r["conclusion"]]
    if profit > 0:
        conclusions.append("В целом за день бизнес вышел в плюс.")
    else:
        conclusions.append("В целом за день получен убыток — пересмотрите цены или расходы.")

    day_rentabelnost = (profit / expenses * 100) if expenses > 0 else 0

    result = {
        "mode": "multi",
        "products": per_product_results,
        "sold": total_sold,
        "revenue": total_revenue,
        "expenses": expenses,
        "profit": profit,
        "tax": tax,
        "interest": interest,
        "stockout": any_stockout,
        "event_text": event_text,
        "conclusion": " ".join(conclusions),
        "rentabelnost": day_rentabelnost,
    }

    game["day"] += 1

    return result, None


def process_day(game, form):
    if game["level"] == "hard":
        return process_day_hard(game, form)
    return process_day_single(game, form)


# =========================
# МАРШРУТЫ
# =========================

@app.route("/", methods=["GET", "POST"])
def game_page():
    game = session.get("game")

    if not game:
        return redirect(url_for("select_level"))

    game_over = game["day"] > MAX_DAYS
    result = None
    error = None

    if request.method == "POST" and not game_over:
        result, error = process_day(game, request.form)
        session["game"] = game
        session.modified = True
        game_over = game["day"] > MAX_DAYS

    rating = player_type = None
    rentabelnost = 0
    if game_over and game["total_expenses"] > 0:
        rentabelnost = game["profit"] / game["total_expenses"] * 100
        rating, player_type = final_rating(game["profit"], game["reputation"])

    return render_template_string(TEMPLATE,
        game=game,
        result=result,
        error=error,
        game_over=game_over,
        max_days=MAX_DAYS,
        level_info=LEVELS.get(game["level"]),
        rating=rating,
        player_type=player_type,
        rentabelnost=rentabelnost,
    )


@app.route("/select", methods=["GET"])
def select_level():
    return render_template_string(SELECT_TEMPLATE, levels=LEVELS)


@app.route("/start", methods=["POST"])
def start_game():
    level = request.form.get("level", "medium")
    if level not in LEVELS:
        level = "medium"
    session["game"] = new_game(level)
    return redirect(url_for("game_page"))


@app.route("/reset", methods=["GET", "POST"])
def reset_game():
    session.pop("game", None)
    return redirect(url_for("select_level"))


# =========================
# ШАБЛОНЫ
# =========================

BASE_STYLE = """
<style>
body {
    background: #202225;
    color: white;
    font-family: Arial;
    max-width: 900px;
    margin: auto;
    padding: 40px;
}
.card {
    background: #36393f;
    padding: 25px;
    margin-bottom: 20px;
    border-radius: 12px;
}
.product-block {
    background: #2f3136;
    padding: 15px;
    margin-bottom: 15px;
    border-radius: 10px;
}
input {
    padding: 10px;
    margin: 5px;
    border-radius: 6px;
    border: none;
}
button {
    padding: 12px 25px;
    cursor: pointer;
    border-radius: 8px;
    border: none;
    background: #5865f2;
    color: white;
    font-weight: bold;
}
button:hover { background: #4752c4; }
.error { color: #ff6b6b; font-weight: bold; }
.event { color: #ffd166; font-weight: bold; }
.conclusion { color: #8ecae6; font-style: italic; }
.level-btn {
    display: block;
    width: 100%;
    text-align: left;
    margin-bottom: 15px;
    background: #40444b;
}
.level-btn:hover { background: #5865f2; }
.stars { font-size: 28px; }

.day-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    align-items: start;
}
.day-layout .result-col {
    position: sticky;
    top: 20px;
}
@media (max-width: 720px) {
    .day-layout {
        grid-template-columns: 1fr;
    }
    .day-layout .result-col {
        position: static;
    }
}
</style>
"""

SELECT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>Экономика магазина — выбор уровня</title>""" + BASE_STYLE + """</head>
<body>
<h1>🛒 Экономика магазина</h1>
<div class="card">
<h2>Выберите уровень сложности</h2>
<form method="POST" action="{{ url_for('start_game') }}">
{% for key, info in levels.items() %}
<button class="level-btn" name="level" value="{{ key }}">
<b>{{ info.title }}</b><br>
<span style="font-weight:normal;">{{ info.desc }}</span>
</button>
{% endfor %}
</form>
</div>
</body>
</html>
"""

TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>Экономика магазина</title>""" + BASE_STYLE + """</head>
<body>

<h1>🛒 Экономика магазина</h1>
<p style="opacity:0.7;">Уровень: {{ level_info.title }}</p>

{% if not game_over %}

<div class="day-layout">
<div class="form-col">

<div class="card">
    <h2>День {{ game.day }}/{{ max_days }}</h2>
    <p>💰 Капитал: {{ "{:,.0f}".format(game.balance) }} ₽</p>
    <p>⭐ Репутация: {{ game.reputation }}/100</p>
    {% if game.credit_debt > 0 %}
    <p>💳 Долг по кредиту: {{ "{:,.0f}".format(game.credit_debt) }} ₽</p>
    {% endif %}
</div>

{% if error %}<div class="card"><p class="error">{{ error }}</p></div>{% endif %}

<div class="card">
    <form method="POST">

    {% if game.level == 'hard' %}

        {% for pid, p in game.products.items() %}
        <div class="product-block">
            <h3>{{ p.name }}</h3>
            <p>Себестоимость: {{ "{:,.0f}".format(p.cost) }} ₽ &nbsp;|&nbsp;
               Цена конкурента: {{ "{:,.0f}".format(p.competitor_price) }} ₽ &nbsp;|&nbsp;
               Склад: {{ p.stock }} шт.</p>
            <label>Цена:</label>
            <input type="number" name="price_{{ pid }}" value="{{ p.competitor_price }}" min="1" step="1" required>
            <label>Закупка:</label>
            <input type="number" name="purchase_{{ pid }}" value="30" min="0" step="1" required>
        </div>
        {% endfor %}

        <label>Общий бюджет на рекламу:</label>
        <input type="number" name="advertising" value="2000" min="0" step="1">
        <br>

        <label>Взять кредит (необязательно):</label>
        <input type="number" name="credit" value="0" min="0" step="1">
        <br><br>

    {% else %}

        <h2>🎧 Наушники</h2>
        <p>Себестоимость: {{ "{:,.0f}".format(game.cost) }} ₽</p>
        <p>Цена конкурента: {{ game.competitor_price }} ₽</p>
        <p>📦 Склад: {{ game.stock }} шт.</p>

        <label>Цена:</label>
        <input type="number" name="price" value="1500" min="1" step="1" required>
        <br>

        {% if game.level != 'easy' %}
        <label>Закупка:</label>
        <input type="number" name="purchase" value="50" min="0" step="1" required>
        <br>

        <label>Реклама:</label>
        <input type="number" name="advertising" value="2000" min="0" step="1" required>
        <br>
        {% else %}
        <p style="opacity:0.7;">Закупка происходит автоматически (склад пополняется до 60 шт.)</p>
        {% endif %}

    {% endif %}

        <button>🚀 Начать день</button>
    </form>
</div>

</div>

<div class="result-col">
{% if result %}
<div class="card">
    {% if result.event_text %}
    <p class="event">📢 {{ result.event_text }}</p>
    {% endif %}

    {% if result.mode == 'multi' %}

    <h3>📊 Результат дня</h3>
    {% for pid, r in result.products.items() %}
    <div class="product-block">
        <b>{{ r.name }}</b><br>
        Цена: {{ "{:,.0f}".format(r.price) }} ₽ &nbsp;|&nbsp;
        Спрос: {{ r.demand }} &nbsp;|&nbsp;
        Продано: {{ r.sold }}{% if r.stockout %} (не хватило товара!){% endif %} &nbsp;|&nbsp;
        Выручка: {{ "{:,.0f}".format(r.revenue) }} ₽
        {% if r.conclusion %}<p class="conclusion">💡 {{ r.conclusion }}</p>{% endif %}
    </div>
    {% endfor %}

    <pre>Всего продано: {{ result.sold }}
Общая выручка: {{ "{:,.0f}".format(result.revenue) }} ₽
Общие расходы: {{ "{:,.0f}".format(result.expenses) }} ₽
{% if result.tax > 0 %}из них налог: {{ "{:,.0f}".format(result.tax) }} ₽
{% endif %}{% if result.interest > 0 %}из них проценты по кредиту: {{ "{:,.0f}".format(result.interest) }} ₽
{% endif %}Прибыль за день: {{ "{:,.0f}".format(result.profit) }} ₽
Рентабельность:  {{ "%.0f"|format(result.rentabelnost) }}%

Капитал: {{ "{:,.0f}".format(game.balance) }} ₽</pre>

    <p class="conclusion">💡 {{ result.conclusion }}</p>

    {% else %}

    <pre>📊 Результат дня

Спрос: {{ result.demand }}
Продано: {{ result.sold }}{% if result.stockout %} (товара на складе не хватило!){% endif %}

Выручка: {{ "{:,.0f}".format(result.revenue) }} ₽
Расходы: {{ "{:,.0f}".format(result.expenses) }} ₽
Прибыль: {{ "{:,.0f}".format(result.profit) }} ₽
Рентабельность: {{ "%.0f"|format(result.rentabelnost) }}%

Капитал: {{ "{:,.0f}".format(game.balance) }} ₽</pre>

    {% if result.conclusion %}<p class="conclusion">💡 {{ result.conclusion }}</p>{% endif %}

    {% endif %}
</div>
{% else %}
<div class="card" style="opacity:0.6;">
    <p>Здесь появится результат дня после того, как вы нажмёте «🚀 Начать день».</p>
</div>
{% endif %}
</div>
</div>

{% else %}

<div class="card">
    <h2>🏆 ИГРА ОКОНЧЕНА</h2>
    <pre>
💰 Капитал:       {{ "{:,.0f}".format(game.balance) }} ₽
📈 Прибыль:       {{ "{:,.0f}".format(game.profit) }} ₽
📦 Продано:       {{ game.total_sold }} товара
⭐ Репутация:     {{ game.reputation }}/100

Рентабельность:   {{ "%.0f"|format(rentabelnost) }}%

Оценка:           {{ "⭐" * rating }}

Ваш тип:

{{ player_type }}
    </pre>
    <form method="POST" action="{{ url_for('reset_game') }}">
        <button>🔄 Играть заново</button>
    </form>
</div>

{% endif %}

</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True)