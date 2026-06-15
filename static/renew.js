document.addEventListener("DOMContentLoaded", () => {
  const renewKeyInput = document.getElementById("renewKeyInput");
  const step2 = document.getElementById("step2");
  const copyInvoiceBtn = document.getElementById("copyInvoiceBtn");
  const invoiceValue = document.querySelector(".address-box span");

  function showStep2() {
    if (!step2) {
      return;
    }
    step2.style.display = "block";
    step2.scrollIntoView({ behavior: "smooth" });
  }

  renewKeyInput?.addEventListener("change", () => {
    if (renewKeyInput.files?.length) {
      showStep2();
    }
  });

  copyInvoiceBtn?.addEventListener("click", () => {
    if (!invoiceValue) {
      return;
    }
    navigator.clipboard.writeText(invoiceValue.textContent || "").then(() => {
      const original = copyInvoiceBtn.textContent;
      copyInvoiceBtn.textContent = "✓";
      window.setTimeout(() => {
        copyInvoiceBtn.textContent = original || "Copy";
      }, 2000);
    });
  });
});
