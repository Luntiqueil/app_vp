"use strict";
// Выбор пунктов формы: номер сумки, сервис и разделы с галочками, как в самой форме.
// Использует el() из app.js.

const Choices = (() => {
  let catalog = null;
  let counter = 0;

  async function load(api) {
    catalog = catalog || (await api("/api/form-items"));
    return catalog;
  }

  function serviceFor(chosen, bag) {
    if (chosen) return chosen;
    const number = bag.trim().toUpperCase();
    if (!number) return null;
    return catalog.lavka_prefixes.some((prefix) => number.startsWith(prefix)) ? "Лавка" : "Еда";
  }

  function serviceSwitch(name, initial, onChange) {
    const options = [["", "Авто"], ...catalog.services.map((service) => [service, service])];
    return el("div", { className: "seg", role: "radiogroup" }, ...options.map(([value, text]) => {
      const input = el("input", { type: "radio", name, value, checked: value === (initial || "") });
      input.addEventListener("change", onChange);
      return el("label", {}, input, el("span", { textContent: text }));
    }));
  }

  function section(data, chosen) {
    const box = el("fieldset", { className: "section" }, el("legend", { textContent: data.title }));
    box.dataset.services = JSON.stringify(data.services || []);
    data.items.forEach((item) => {
      const input = el("input", { type: "checkbox", value: item.value, checked: chosen.has(item.value) });
      if (data.choice) {
        // в выпадающем списке формы бывает только один ответ
        input.addEventListener("change", () => {
          if (!input.checked) return;
          box.querySelectorAll("input").forEach((other) => { if (other !== input) other.checked = false; });
        });
      }
      box.append(el("label", { className: "check" }, input, el("span", { textContent: item.label })));
    });
    return box;
  }

  // initial: {bag, service, ticks}. Возвращает {node, read, reset}.
  function build(initial = {}) {
    const name = `service-${++counter}`;
    const chosen = new Set(initial.ticks || []);
    const bag = el("input", {
      type: "text", value: initial.bag || "", placeholder: "Распознаю по фото",
      autocapitalize: "characters", autocomplete: "off", spellcheck: false,
    });
    const sections = catalog.sections.map((data) => section(data, chosen));
    const hint = el("p", { className: "hint muted" });
    const node = el("div", { className: "choices" },
      el("label", { className: "field", textContent: "Номер сумки" }), bag,
      el("span", { className: "field", textContent: "Сервис" }),
      serviceSwitch(name, initial.service, update), hint,
      ...sections,
    );

    function chosenService() {
      return node.querySelector(`input[name="${name}"]:checked`).value;
    }

    function update() {
      const service = serviceFor(chosenService(), bag.value);
      hint.textContent = chosenService() ? "" : service ? `По номеру сумки: ${service}` : "Определю по номеру сумки";
      sections.forEach((box) => {
        const only = JSON.parse(box.dataset.services);
        box.hidden = Boolean(service && only.length && !only.includes(service));
        if (box.hidden) box.querySelectorAll("input").forEach((input) => { input.checked = false; });
      });
    }

    function read() {
      const ticks = sections.filter((box) => !box.hidden)
        .flatMap((box) => [...box.querySelectorAll("input:checked")].map((input) => input.value));
      return { bag: bag.value.trim(), service: chosenService(), ticks };
    }

    function reset() {
      bag.value = "";
      node.querySelector(`input[name="${name}"][value=""]`).checked = true;
      node.querySelectorAll(".section input").forEach((input) => { input.checked = false; });
      update();
    }

    bag.addEventListener("input", update);
    update();
    return { node, read, reset };
  }

  function append(params, choice) {
    params.append("bag", choice.bag);
    params.append("service", choice.service);
    choice.ticks.forEach((tick) => params.append("ticks", tick));
  }

  return { load, build, append };
})();
