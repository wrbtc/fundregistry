document.addEventListener("DOMContentLoaded", () => {
  function setTemporaryLabel(button, label) {
    const original = button.textContent;
    button.textContent = label;
    window.setTimeout(() => {
      button.textContent = original || "Copy";
    }, 2000);
  }

  document.querySelectorAll("[data-tab-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.getAttribute("data-tab-target");
      document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((content) => content.classList.remove("active"));
      document.getElementById(`tab-${target}`)?.classList.add("active");
      button.classList.add("active");
    });
  });

  document.querySelectorAll("[data-copy-text]").forEach((button) => {
    button.addEventListener("click", () => {
      navigator.clipboard.writeText(button.getAttribute("data-copy-text") || "").then(() => {
        setTemporaryLabel(button, "✓");
      });
    });
  });

  document.querySelectorAll("[data-copy-link]").forEach((button) => {
    button.addEventListener("click", () => {
      navigator.clipboard.writeText(button.getAttribute("data-copy-link") || "").then(() => {
        setTemporaryLabel(button, "✓ Copied");
      });
    });
  });

  document.querySelectorAll("[data-copy-button-html]").forEach((button) => {
    button.addEventListener("click", () => {
      navigator.clipboard.writeText(button.getAttribute("data-copy-button-html") || "").then(() => {
        setTemporaryLabel(button, "✓ Copied");
      });
    });
  });
});
