const API = "/api";
const REFRESH_MS = 2000;
const CITY_CENTER = [55.7558, 37.6173];

const STATUS_RU = {
    new: "новый",
    searching: "поиск",
    assigned: "назначен",
    in_progress: "в пути",
    completed: "выполнен",
    cancelled: "отменён",
    no_drivers: "нет водителей",
};
const DRIVER_STATUS_RU = { free: "свободен", busy: "занят", offline: "не на линии" };
const TARIFF_RU = { economy: "Эконом", comfort: "Комфорт", business: "Бизнес" };
const DRIVER_COLOR = { free: "#4fb37a", busy: "#d6302a", offline: "#5d6169" };
const ORDER_COLOR = "#e8b84a";
const fmt = (value, digits) => value.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
let driverIndex = new Map();

let session = loadSession();
let map = null;
let driverLayer, orderLayer, draftLayer;
let draft = { pickup: null, dropoff: null };
let timer = null;

// ---------- Сессия ----------

function loadSession() {
    try {
        return JSON.parse(sessionStorage.getItem("taxi-session"));
    } catch {
        return null;
    }
}

function saveSession(value) {
    session = value;
    try {
        if (value) {
            sessionStorage.setItem("taxi-session", JSON.stringify(value));
        } else {
            sessionStorage.removeItem("taxi-session");
        }
    } catch {
        // хранилище недоступно — сессия живёт до перезагрузки страницы
    }
}

function esc(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
}

async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    if (session) {
        headers.Authorization = "Bearer " + session.token;
    }
    const resp = await fetch(API + path, { ...options, headers });
    if (resp.status === 401 && session) {
        logout();
        throw new Error("Сессия истекла, выполните вход повторно");
    }
    if (!resp.ok) {
        let message = resp.status + "";
        try {
            const body = await resp.json();
            message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        } catch {
            // ответ без JSON
        }
        throw new Error(message);
    }
    return resp.status === 204 ? null : resp.json();
}

// ---------- Вход и выход ----------

document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const error = document.getElementById("login-error");
    error.textContent = "";
    try {
        const result = await api("/auth/login", {
            method: "POST",
            body: JSON.stringify({
                username: document.getElementById("login-username").value,
                password: document.getElementById("login-password").value,
            }),
        });
        saveSession({ token: result.access_token, user: result.user });
        startApp();
    } catch (err) {
        error.textContent = err.message;
    }
});

function logout() {
    saveSession(null);
    clearInterval(timer);
    document.getElementById("app").hidden = true;
    document.getElementById("login-screen").hidden = false;
}

document.getElementById("logout").addEventListener("click", logout);

function startApp() {
    document.getElementById("login-screen").hidden = true;
    document.getElementById("app").hidden = false;
    const { user } = session;
    document.getElementById("user-name").textContent =
        `${user.full_name} (${user.role === "admin" ? "администратор" : "диспетчер"})`;
    for (const el of document.querySelectorAll("[data-admin]")) {
        el.hidden = user.role !== "admin";
    }
    initMap();
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, REFRESH_MS);
}

// ---------- Вкладки ----------

for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
        for (const t of document.querySelectorAll(".tab")) {
            t.classList.toggle("active", t === tab);
        }
        for (const page of document.querySelectorAll(".tab-page")) {
            page.hidden = page.id !== tab.dataset.tab;
        }
        if (tab.dataset.tab === "orders-tab" && map) {
            map.invalidateSize();
        }
        refresh();
    });
}

// ---------- Карта и заказы ----------

function initMap() {
    if (map) return;
    map = L.map("map").setView(CITY_CENTER, 12);
    map.attributionControl.setPrefix(false);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap",
    }).addTo(map);
    driverLayer = L.layerGroup().addTo(map);
    orderLayer = L.layerGroup().addTo(map);
    draftLayer = L.layerGroup().addTo(map);
    map.on("click", onMapClick);
}

const hint = document.getElementById("hint");

function resetDraft() {
    draft = { pickup: null, dropoff: null };
    draftLayer.clearLayers();
    hint.textContent = "кликните на карте точку подачи";
}

async function onMapClick(e) {
    const point = { lat: e.latlng.lat, lon: e.latlng.lng };
    if (!draft.pickup) {
        draft.pickup = point;
        L.marker(e.latlng).bindTooltip("Подача").addTo(draftLayer);
        hint.textContent = "теперь кликните точку назначения";
        return;
    }
    draft.dropoff = point;
    L.marker(e.latlng).bindTooltip("Назначение").addTo(draftLayer);
    try {
        const order = await api("/orders", {
            method: "POST",
            body: JSON.stringify({
                passenger_name: "Заказ диспетчера",
                tariff: document.getElementById("tariff").value,
                pickup: draft.pickup,
                dropoff: draft.dropoff,
            }),
        });
        hint.textContent = `создан заказ №${order.id}: ${order.distance_km} км, ${order.price} ₽`;
    } catch (err) {
        hint.textContent = "ошибка: " + err.message;
    }
    setTimeout(resetDraft, 2500);
    refresh();
}

document.getElementById("reset").addEventListener("click", resetDraft);

function renderMapDrivers(drivers) {
    driverIndex = new Map(drivers.map((d) => [d.id, d]));
    driverLayer.clearLayers();
    for (const d of drivers) {
        L.circleMarker([d.lat, d.lon], {
            radius: 6,
            color: DRIVER_COLOR[d.status],
            fillColor: DRIVER_COLOR[d.status],
            fillOpacity: 0.9,
            weight: 2,
            opacity: 0.35,
        })
            .bindTooltip(`${esc(d.name)}<br>${esc(d.car)}, ${esc(d.plate)}<br>` +
                `${TARIFF_RU[d.car_class]} · ${DRIVER_STATUS_RU[d.status]}`)
            .addTo(driverLayer);
    }
}

function renderOrders(orders) {
    orderLayer.clearLayers();
    const tbody = document.getElementById("orders");
    tbody.innerHTML = "";
    for (const o of orders) {
        const active = ["new", "searching", "assigned"].includes(o.status);
        if (active) {
            L.circleMarker([o.pickup_lat, o.pickup_lon], {
                radius: 5,
                color: ORDER_COLOR,
                fillColor: ORDER_COLOR,
                fillOpacity: 0.6,
                weight: 2,
            })
                .bindTooltip(`Заказ №${o.id} · ${STATUS_RU[o.status]}`)
                .addTo(orderLayer);
        }
        const tr = document.createElement("tr");
        const driver = driverIndex.get(o.driver_id);
        const driverCell = driver
            ? `<span title="${esc(driver.plate)}">${esc(shortName(driver.name))}</span>`
            : `<span class="muted">${o.driver_id ? "№ " + o.driver_id : "нет"}</span>`;
        tr.innerHTML = `
            <td class="pos num">${o.id}</td>
            <td>${esc(o.passenger_name)}</td>
            <td>${TARIFF_RU[o.tariff]}</td>
            <td class="num">${fmt(Number(o.distance_km), 1)}</td>
            <td class="num">${Number(o.price).toLocaleString("ru-RU")} ₽</td>
            <td><span class="status ${o.status}"><i class="dot ${o.status}"></i>${STATUS_RU[o.status]}</span></td>
            <td>${driverCell}</td>
            <td>${active ? `<button data-cancel="${o.id}">Отмена</button>` : ""}</td>`;
        tbody.appendChild(tr);
    }
}

document.getElementById("orders").addEventListener("click", async (e) => {
    const id = e.target.dataset.cancel;
    if (!id) return;
    try {
        await api(`/orders/${id}/cancel`, { method: "POST" });
    } catch (err) {
        console.warn(err);
    }
    refresh();
});

// «Иванов Пётр Сергеевич» -> «Иванов П.»
function shortName(name) {
    const parts = String(name).trim().split(/\s+/);
    return parts.length > 1 ? `${parts[0]} ${parts[1][0]}.` : parts[0];
}

function renderStats(stats) {
    for (const key of ["searching", "assigned", "in_progress", "completed", "no_drivers"]) {
        document.getElementById("s-" + key).textContent = stats.by_status[key] ?? 0;
    }
    document.getElementById("s-assign").textContent =
        stats.avg_assign_sec != null ? fmt(stats.avg_assign_sec, 1) + " с" : "нет данных";
    document.getElementById("s-pickup").textContent =
        stats.avg_pickup_km != null ? fmt(stats.avg_pickup_km, 2) + " км" : "нет данных";
    document.getElementById("s-revenue").textContent =
        Math.round(stats.revenue).toLocaleString("ru-RU") + " ₽";
}

// ---------- Управление водителями (CRUD) ----------

const driverForm = document.getElementById("driver-form");

function fillDriverForm(driver) {
    document.getElementById("driver-form-title").textContent =
        driver ? `Водитель №${driver.id}` : "Новый водитель";
    document.getElementById("d-id").value = driver ? driver.id : "";
    document.getElementById("d-name").value = driver ? driver.name : "";
    document.getElementById("d-car").value = driver ? driver.car : "";
    document.getElementById("d-plate").value = driver ? driver.plate : "";
    document.getElementById("d-class").value = driver ? driver.car_class : "economy";
    document.getElementById("driver-error").textContent = "";
    for (const row of document.querySelectorAll("#drivers tr")) {
        row.classList.toggle("editing", Boolean(driver) && row.dataset.id === String(driver.id));
    }
}

driverForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("d-id").value;
    const body = {
        name: document.getElementById("d-name").value,
        car: document.getElementById("d-car").value,
        plate: document.getElementById("d-plate").value,
        car_class: document.getElementById("d-class").value,
    };
    try {
        if (id) {
            await api(`/drivers/${id}`, { method: "PUT", body: JSON.stringify(body) });
        } else {
            await api("/drivers", {
                method: "POST",
                body: JSON.stringify({ ...body, lat: CITY_CENTER[0], lon: CITY_CENTER[1] }),
            });
        }
        fillDriverForm(null);
        refresh();
    } catch (err) {
        document.getElementById("driver-error").textContent = err.message;
    }
});

document.getElementById("d-cancel").addEventListener("click", () => fillDriverForm(null));

let driversCache = [];

function renderDriverTable(drivers) {
    driversCache = drivers;
    const tbody = document.getElementById("drivers");
    tbody.innerHTML = "";
    for (const d of drivers) {
        const tr = document.createElement("tr");
        tr.dataset.id = d.id;
        tr.classList.toggle("editing", document.getElementById("d-id").value === String(d.id));
        tr.innerHTML = `
            <td class="pos num">${d.id}</td>
            <td>${esc(d.name)}</td>
            <td>${esc(d.car)}</td>
            <td><span class="plate">${esc(d.plate)}</span></td>
            <td>${TARIFF_RU[d.car_class]}</td>
            <td><span class="status"><i class="dot ${d.status}"></i>${DRIVER_STATUS_RU[d.status]}</span></td>
            <td>
                <button data-edit="${d.id}">Изменить</button>
                <button data-delete="${d.id}">Удалить</button>
            </td>`;
        tbody.appendChild(tr);
    }
}

document.getElementById("drivers").addEventListener("click", async (e) => {
    const editId = e.target.dataset.edit;
    const deleteId = e.target.dataset.delete;
    if (editId) {
        fillDriverForm(driversCache.find((d) => String(d.id) === editId));
    }
    if (deleteId) {
        try {
            await api(`/drivers/${deleteId}`, { method: "DELETE" });
        } catch (err) {
            document.getElementById("driver-error").textContent = err.message;
        }
        refresh();
    }
});

// ---------- Обновление данных ----------

async function refresh() {
    if (!session) return;
    const conn = document.getElementById("conn");
    try {
        const driversVisible = !document.getElementById("drivers-tab").hidden;
        if (driversVisible) {
            renderDriverTable(await api("/drivers"));
        } else {
            const [drivers, orders, stats] = await Promise.all([
                api("/drivers"),
                api("/orders?limit=40"),
                api("/orders/stats"),
            ]);
            renderMapDrivers(drivers);
            renderOrders(orders);
            renderStats(stats);
        }
        conn.textContent = "обновлено " + new Date().toLocaleTimeString("ru-RU");
        conn.className = "conn ok";
    } catch (err) {
        conn.textContent = "нет связи с API";
        conn.className = "conn err";
    }
}

if (session) {
    startApp();
}
