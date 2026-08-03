"use strict";

/**
 * preload.js — скрипт предзагрузки Electron.
 *
 * Выполняется в изолированном контексте рендерера до загрузки страницы.
 * Единственная его задача — пометить документ атрибутом data-electron,
 * чтобы фронтенд мог адаптировать UI под десктопный режим.
 *
 * Намеренно не предоставляет никаких Node.js API в рендерер:
 * contextIsolation: true + sandbox: true обеспечивают безопасность.
 */

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.setAttribute("data-electron", "true");
});
