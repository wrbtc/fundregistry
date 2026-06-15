document.addEventListener("DOMContentLoaded", function () {
  var banner = document.querySelector("[data-beta-banner]");
  var dismissBtn = document.querySelector("[data-beta-banner-dismiss]");
  var storageKey = "fr-beta-banner-dismissed";
  if (!banner) return;
  try {
    if (window.sessionStorage.getItem(storageKey) === "1") {
      banner.hidden = true;
      return;
    }
  } catch (error) {}
  if (dismissBtn) {
    dismissBtn.addEventListener("click", function () {
      banner.hidden = true;
      try { window.sessionStorage.setItem(storageKey, "1"); } catch (error) {}
    });
  }
});
