"use strict";

const POLL_MS = 3000;
const ACTIVE = new Set(["queued", "processing"]);
const STATUS_TEXT = {
  queued: "В очереди",
  processing: "Обрабатывается",
  done: "Отправлено",
  review: "Нужна проверка",
  failed: "Ошибка",
};

const photosInput = document.getElementById("photos");
const noteInput = document.getElementById("note");
const sendButton = document.getElementById("send");
const errorBox = document.getElementById("error");
const previews = document.getElementById("previews");
const pickText = document.getElementById("pick-text");
const jobsList = document.getElementById("jobs");
const emptyText = document.getElementById("empty");

let maxPhotos = 10;
let lastRendered = "";
let mainChoices = null;

function showError(text) {
  errorBox.textContent = text || "";
  errorBox.hidden = !text;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("Нет связи с приложением. Проверьте интернет и попробуйте ещё раз.");
  }
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("Нужен вход.");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Что-то пошло не так.");
  return body;
}

function renderPreviews() {
  const files = [...photosInput.files];
  previews.replaceChildren(...files.map((file) => {
    const url = URL.createObjectURL(file);
    return el("img", { src: url, alt: "", onload: () => URL.revokeObjectURL(url) });
  }));
  previews.hidden = !files.length;
  pickText.textContent = files.length ? `Выбрано фото: ${files.length}` : "Выбрать фото";
  sendButton.disabled = !files.length;
  showError(files.length > maxPhotos ? `Не больше ${maxPhotos} фото в одном отчёте.` : "");
}

function retryForm(job) {
  const form = el("form");
  const choices = Choices.build({ bag: job.bag, service: job.service_choice, ticks: job.ticks });
  const note = el("textarea", { rows: 2, maxLength: 500, value: job.note });
  const error = el("p", { className: "error", hidden: true });
  const button = el("button", { type: "submit", textContent: "Отправить" });
  form.append(
    choices.node,
    el("label", { className: "field", textContent: "Комментарий в Telegram" }), note,
    button, error,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    const body = new URLSearchParams({ note: note.value, ticks_sent: "1" });
    Choices.append(body, choices.read());
    try {
      await api(`/api/jobs/${job.id}/retry`, { method: "POST", body });
      lastRendered = "";
      await refresh();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      button.disabled = false;
    }
  });
  return form;
}

function renderJob(job) {
  const time = new Date(job.created_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const head = el("div", { className: "job-head" },
    el("img", { src: `/api/jobs/${job.id}/photos/1`, alt: "", loading: "lazy" }),
    el("div", {},
      el("div", { className: "job-title", textContent: job.bag || `Фото: ${job.photo_count}` }),
      el("div", { className: "job-time", textContent: job.service ? `${job.service} · ${time}` : time }),
      el("span", { className: `badge ${job.status}`, textContent: STATUS_TEXT[job.status] || job.status }),
    ),
  );
  const item = el("li", { className: "job" }, head);
  const needsPerson = job.status === "review" || job.status === "failed";
  if (needsPerson || job.has_screenshot) {
    const body = el("div", { className: "job-body" });
    if (job.message && needsPerson) body.append(el("p", { textContent: job.message }));
    if (job.problems.length && needsPerson) {
      body.append(el("ul", {}, ...job.problems.map((text) => el("li", { textContent: text }))));
    }
    if (job.has_screenshot) {
      body.append(el("a", { href: `/api/jobs/${job.id}/screenshot`, target: "_blank", rel: "noopener", textContent: "Снимок формы" }));
    }
    if (needsPerson) body.append(retryForm(job));
    item.append(body);
  }
  return item;
}

let pollTimer = null;

async function refresh() {
  clearTimeout(pollTimer);
  try {
    const { jobs, limits } = await api("/api/jobs");
    maxPhotos = limits.photos;
    const snapshot = JSON.stringify(jobs);
    // не перерисовываем без изменений, иначе сбросится текст, который человек сейчас вводит
    if (snapshot !== lastRendered) {
      lastRendered = snapshot;
      jobsList.replaceChildren(...jobs.map(renderJob));
    }
    emptyText.hidden = jobs.length > 0;
  } catch {
    // следующий опрос попробует снова; ошибки отправки показываются отдельно
  }
  pollTimer = setTimeout(refresh, POLL_MS);
}

photosInput.addEventListener("change", renderPreviews);

document.getElementById("report").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = [...photosInput.files];
  if (!files.length || files.length > maxPhotos) return;
  const body = new FormData();
  files.forEach((file) => body.append("photos", file));
  body.append("note", noteInput.value);
  if (mainChoices) Choices.append(body, mainChoices.read());
  sendButton.disabled = true;
  sendButton.textContent = "Загружаю…";
  showError("");
  try {
    const { duplicate } = await api("/api/jobs", { method: "POST", body });
    photosInput.value = "";
    noteInput.value = "";
    if (mainChoices) mainChoices.reset();
    renderPreviews();
    if (duplicate) showError("Эти фото уже загружены, новый отчёт не создан.");
    await refresh();
  } catch (failure) {
    showError(failure.message);  // фото остаются выбранными, можно просто нажать ещё раз
    sendButton.disabled = false;
  } finally {
    sendButton.textContent = "Отправить";
  }
});

async function start() {
  try {
    await Choices.load(api);
    mainChoices = Choices.build();
    document.getElementById("choices").replaceChildren(mainChoices.node);
  } catch (failure) {
    document.getElementById("choices").replaceChildren(
      el("p", { className: "error", textContent: `Пункты формы не загрузились: ${failure.message}` }),
    );
  }
  refresh();  // карточки «Нужна проверка» строятся с пунктами, поэтому после загрузки
}

start();
