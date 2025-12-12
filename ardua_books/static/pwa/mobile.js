// static/pwa/mobile.js

function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(";").shift();
  return null;
}

const csrftoken = getCookie("csrftoken");
const apiBase = "/m/api";

let mobileMeta = { clients: [], categories: [] };

function setStatus(message, isError = false) {
  const el = document.getElementById("mobile-status");
  if (!el) return;
  el.textContent = message || "";
  el.classList.toggle("mobile-status-error", !!isError);
}

async function getJson(path) {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: "same-origin",
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text.substring(0, 200)}`);
  }
  return response.json();
}

async function postJson(path, payload) {
  const response = await fetch(`${apiBase}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrftoken || "",
    },
    credentials: "same-origin",
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text.substring(0, 200)}`);
  }
  return response.json();
}

function todayIso() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function renderClientOptions(selectId, selectedId) {
  const select = document.getElementById(selectId);
  if (!select) return;
  select.innerHTML = "";
  mobileMeta.clients.forEach((c, index) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    if (selectedId && String(selectedId) === String(c.id)) {
      opt.selected = true;
    } else if (!selectedId && index === 0) {
      opt.selected = true;
    }
    select.appendChild(opt);
  });
}

function renderCategoryOptions(selectId, selectedId) {
  const select = document.getElementById(selectId);
  if (!select) return;
  select.innerHTML = "";
  mobileMeta.categories.forEach((cat, index) => {
    const opt = document.createElement("option");
    opt.value = cat.id;
    opt.textContent = cat.name;
    if (selectedId && String(selectedId) === String(cat.id)) {
      opt.selected = true;
    } else if (!selectedId && index === 0) {
      opt.selected = true;
    }
    select.appendChild(opt);
  });
}

function renderTimeForm() {
  const container = document.getElementById("mobile-form-container");
  if (!container) return;
  container.innerHTML = `
    <h3>New Time Entry</h3>
    <form id="mobile-time-form" class="mobile-form">
      <label for="mobile-time-client">Client</label>
      <select id="mobile-time-client" name="client_id"></select>

      <label for="mobile-time-date">Date</label>
      <input type="date" id="mobile-time-date" name="date" value="${todayIso()}">

      <label for="mobile-time-hours">Hours</label>
      <input type="number" id="mobile-time-hours" name="hours" step="0.25" min="0">

      <label for="mobile-time-description">Description</label>
      <textarea id="mobile-time-description" name="description" rows="3"></textarea>

      <button type="submit" class="btn btn-primary">Save</button>
    </form>
  `;
  renderClientOptions("mobile-time-client");
  const form = document.getElementById("mobile-time-form");
  form.addEventListener("submit", submitTimeEntry);
}

function renderExpenseForm() {
  const container = document.getElementById("mobile-form-container");
  if (!container) return;
  container.innerHTML = `
    <h3>New Expense</h3>
    <form id="mobile-expense-form" class="mobile-form">
      <label for="mobile-expense-client">Client</label>
      <select id="mobile-expense-client" name="client_id"></select>

      <label for="mobile-expense-date">Date</label>
      <input type="date" id="mobile-expense-date" name="date" value="${todayIso()}">

      <label for="mobile-expense-category">Category</label>
      <select id="mobile-expense-category" name="category_id"></select>

      <label for="mobile-expense-amount">Amount</label>
      <input type="number" id="mobile-expense-amount" name="amount" step="0.01" min="0">

      <label for="mobile-expense-description">Description</label>
      <textarea id="mobile-expense-description" name="description" rows="3"></textarea>

      <button type="submit" class="btn btn-primary">Save</button>
    </form>
  `;
  renderClientOptions("mobile-expense-client");
  renderCategoryOptions("mobile-expense-category");
  const form = document.getElementById("mobile-expense-form");
  form.addEventListener("submit", submitExpense);
}

async function submitTimeEntry(event) {
  event.preventDefault();
  setStatus("Saving…");
  const clientId = document.getElementById("mobile-time-client").value;
  const date = document.getElementById("mobile-time-date").value;
  const hours = document.getElementById("mobile-time-hours").value;
  const description = document.getElementById("mobile-time-description").value;
  try {
    await postJson("/time-entries/", { client_id: clientId, date, hours, description });
    setStatus("Time entry saved.");
    document.getElementById("mobile-time-hours").value = "";
    document.getElementById("mobile-time-description").value = "";
  } catch (err) {
    console.error(err);
    setStatus(err.message || "Network error.", true);
  }
}

async function submitExpense(event) {
  event.preventDefault();
  setStatus("Saving…");
  const clientId = document.getElementById("mobile-expense-client").value;
  const categoryId = document.getElementById("mobile-expense-category").value;
  const date = document.getElementById("mobile-expense-date").value;
  const amount = document.getElementById("mobile-expense-amount").value;
  const description = document.getElementById("mobile-expense-description").value;
  try {
    await postJson("/expenses/", { client_id: clientId, category_id: categoryId, date, amount, description });
    setStatus("Expense saved.");
    document.getElementById("mobile-expense-amount").value = "";
    document.getElementById("mobile-expense-description").value = "";
  } catch (err) {
    console.error(err);
    setStatus(err.message || "Network error.", true);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    setStatus("Loading…");
    mobileMeta = await getJson("/meta/");
    if (!mobileMeta.clients.length) {
      setStatus("No clients defined. Add at least one client in the desktop app.", true);
      return;
    }
    setStatus("");
  } catch (err) {
    console.error(err);
    setStatus(err.message || "Failed to load metadata.", true);
    return;
  }

  const btnTime = document.getElementById("btn-time");
  const btnExpense = document.getElementById("btn-expense");

  if (btnTime) {
    btnTime.addEventListener("click", (e) => {
      e.preventDefault();
      renderTimeForm();
    });
  }
  if (btnExpense) {
    btnExpense.addEventListener("click", (e) => {
      e.preventDefault();
      renderExpenseForm();
    });
  }

  // Default screen: time entry form
  renderTimeForm();
});

