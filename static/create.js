document.addEventListener("DOMContentLoaded", () => {
  const CREATE_RESULT_KEY = "fundRegistryCreateResultV1";
  const vanitySlug = document.getElementById("vanitySlug");
  const photoInput = document.getElementById("photoInput");
  const photoPrompt = document.getElementById("photoPrompt");
  const form = document.getElementById("createForm");
  const submitBtn = document.getElementById("submitBtn");

  function showVanityInput() {
    const checked = document.querySelector('input[name="tier"]:checked');
    if (!checked || !vanitySlug) {
      return;
    }
    vanitySlug.style.display = checked.value === "tier3" ? "block" : "none";
  }

  function previewPhoto(file) {
    if (!photoPrompt) {
      return;
    }
    if (!file) {
      photoPrompt.innerHTML = "";
      return;
    }
    if (file.size > 200 * 1024) {
      window.alert("Photo must be under 200KB");
      if (photoInput) {
        photoInput.value = "";
      }
      return;
    }
    const name = document.createElement("div");
    name.style.fontSize = "0.875rem";
    name.style.color = "var(--accent)";
    name.textContent = `✓ ${file.name}`;
    photoPrompt.replaceChildren(name);
  }

  function validateBitcoinAddress(value) {
    return /^(1[1-9A-HJ-NP-Za-km-z]{25,34}|3[1-9A-HJ-NP-Za-km-z]{25,34}|bc1[a-zA-HJ-NP-Z0-9]{25,90})$/.test(value);
  }

  function api(method, path, body) {
    return fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (response) => {
      let data = {};
      try {
        data = await response.json();
      } catch (_error) {
        data = {};
      }
      if (!response.ok) {
        throw new Error(data.detail || "Request failed");
      }
      return data;
    });
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (event) => {
        const value = String(event.target?.result || "");
        const encoded = value.includes(",") ? value.split(",")[1] : value;
        resolve(encoded);
      };
      reader.onerror = () => reject(new Error("Could not read the photo."));
      reader.readAsDataURL(file);
    });
  }

  async function maybeUploadPhoto(result, file) {
    if (!file || !result?.page?.id || !result?.campaign_key) {
      return result;
    }
    const encoded = await fileToBase64(file);
    const response = await api("POST", `/v1/pages/${result.page.id}/photo`, {
      campaign_key: result.campaign_key,
      content_type: file.type,
      image_base64: encoded,
    });
    if (response?.page) {
      result.page = response.page;
    }
    return result;
  }

  async function validateAndSubmit(event) {
    event.preventDefault();
    const title = document.getElementById("title")?.value.trim() || "";
    const description = document.getElementById("description")?.value.trim() || "";
    const btcAddress = document.getElementById("btcAddress")?.value.trim() || "";
    const tier = document.querySelector('input[name="tier"]:checked')?.value || "free";
    const vanity = document.getElementById("slug")?.value.trim() || "";
    const photoFile = photoInput?.files?.[0] || null;
    const errors = [];

    if (title.length < 5) {
      errors.push("Campaign title must be at least 5 characters.");
    }
    if (description.length < 50) {
      errors.push(`Description must be at least 50 characters. Currently: ${description.length}`);
    }
    if (!btcAddress) {
      errors.push("Bitcoin address is required.");
    } else if (!validateBitcoinAddress(btcAddress)) {
      errors.push("Invalid Bitcoin address format.");
    }
    if (tier === "tier3" && !vanity) {
      errors.push("Custom URL is required for tier3.");
    }
    if (photoFile && photoFile.size > 200 * 1024) {
      errors.push("Photo must be under 200KB.");
    }

    if (errors.length > 0) {
      window.alert(`Please fix the following:\n\n• ${errors.join("\n• ")}`);
      return;
    }

    let confirmation = "Please confirm:\n\n";
    confirmation += `Bitcoin address:\n${btcAddress}\n\n`;
    confirmation += "This Bitcoin address cannot be changed after creation.\n\nProceed?";
    if (!window.confirm(confirmation)) {
      return;
    }

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Creating...";
    }

    try {
      let result = await api("POST", "/v1/pages", {
        title,
        description,
        btc_address: btcAddress,
        tier,
        vanity_slug: tier === "tier3" ? vanity : undefined,
      });
      result = await maybeUploadPhoto(result, photoFile);
      window.sessionStorage.setItem(CREATE_RESULT_KEY, JSON.stringify(result));
      window.location.href = "/campaign-key";
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Could not create the page.");
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Create funding page";
      }
    }
  }

  document.querySelectorAll('input[name="tier"]').forEach((radio) => {
    radio.addEventListener("change", showVanityInput);
  });
  showVanityInput();

  if (photoInput) {
    photoInput.addEventListener("change", () => {
      previewPhoto(photoInput.files?.[0] || null);
    });
  }

  if (form) {
    form.addEventListener("submit", validateAndSubmit);
  }
});
