document.addEventListener("DOMContentLoaded", () => {
  const button = document.getElementById("dismissExpiredOverlayBtn");
  const overlay = document.getElementById("expiredOverlay");
  const content = document.getElementById("expiredContent");
  button?.addEventListener("click", () => {
    if (overlay) {
      overlay.style.display = "none";
    }
    if (content) {
      content.style.opacity = "1";
      content.style.pointerEvents = "auto";
    }
  });
});
