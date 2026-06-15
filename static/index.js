document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("searchForm");
  const input = document.getElementById("searchInput");
  const counter = document.getElementById("pageCounter");
  const counterValue = document.getElementById("pageCounterValue");

  if (counter && counterValue) {
    fetch("/v1/stats", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`stats ${response.status}`);
        }
        return response.json();
      })
      .then((payload) => {
        const totalPages = Number(payload && payload.total_pages);
        if (!Number.isFinite(totalPages) || totalPages < 0) {
          return;
        }
        counterValue.textContent = totalPages.toLocaleString("en-US");
        counter.hidden = false;
      })
      .catch(() => {
        counter.hidden = true;
      });
  }

  if (!form || !input) {
    return;
  }

  form.addEventListener("submit", (event) => {
    const query = input.value.trim();
    if (!query) {
      event.preventDefault();
      return;
    }
    input.value = query;
  });
});
