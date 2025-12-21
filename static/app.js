function money(n) {
  const v = Number(n || 0);
  return v.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

async function postJson(url, data, method = "POST") {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = json.error || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return json;
}

document.addEventListener("DOMContentLoaded", () => {
  const estimateForm = document.getElementById("estimateForm");
  const saveNetForm = document.getElementById("saveNetForm");
  const editLatestForm = document.getElementById("editLatestForm");

  if (estimateForm) {
    estimateForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(estimateForm);

      const payload = {
        gross_annual: fd.get("gross_annual"),
        filing_status: fd.get("filing_status"),
        state: fd.get("state"),
      };

      try {
        const out = await postJson("/api/estimate", payload);

        document.getElementById("estimateResult").classList.remove("d-none");
        document.getElementById("fedTax").textContent = money(out.federal.federal_tax);
        document.getElementById("fedDed").textContent = money(out.federal.standard_deduction);
        document.getElementById("stateTax").textContent = money(out.state_detail.state_tax);
        document.getElementById("stateCode").textContent = out.state;
        document.getElementById("netAnnual").textContent = money(out.net_annual);
      } catch (err) {
        alert(err.message);
      }
    });
  }

  if (saveNetForm) {
    saveNetForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(saveNetForm);

      const payload = {
        label: fd.get("label"),
        net_amount: fd.get("net_amount"),
        frequency: fd.get("frequency"),
      };

      try {
        await postJson("/api/net_income", payload);
        // simple refresh so latest entry + dashboard activity update
        window.location.reload();
      } catch (err) {
        alert(err.message);
      }
    });
  }

  if (editLatestForm) {
    editLatestForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(editLatestForm);

      const payload = {
        label: fd.get("label"),
        net_amount: fd.get("net_amount"),
        frequency: fd.get("frequency"),
      };

      const errBox = document.getElementById("editError");
      errBox.classList.add("d-none");
      errBox.textContent = "";

      try {
        await postJson("/api/net_income/latest", payload, "PUT");
        window.location.reload();
      } catch (err) {
        errBox.textContent = err.message;
        errBox.classList.remove("d-none");
      }
    });
  }
});
