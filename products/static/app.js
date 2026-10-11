document.addEventListener("input", (event) => {
  if (event.target.matches("[name=field_value]")) {
    event.target.closest(".field-row").querySelector("[name=field_present]").value = "1";
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[name=field_type]") && event.target.value !== "select") {
    event.target.closest(".field-row").querySelector("[name=field_options]").value = "";
  }
});

document.addEventListener("click", (event) => {
  if (event.target.matches(".remove-row")) {
    event.target.closest(".field-row").remove();
  }
  if (event.target.id === "add-field") {
    const row = document.createElement("div");
    row.className = "field-row";
    const value = event.target.form.classList.contains("editor") && event.target.form.querySelector("[name=field_present]");
    row.innerHTML = `<input type="hidden" name="field_key" value="">
      <input name="field_label" required placeholder="Label">
      <select name="field_type"><option>text</option><option>number</option><option>boolean</option><option>date</option><option>select</option><option>text[]</option></select>
      <input name="field_options" placeholder="Select options, comma separated">
      ${value ? '<input name="field_value" placeholder="Value"><input type="hidden" name="field_present" value="0">' : ""}
      <button type="button" class="remove-row">Remove</button>`;
    document.querySelector("#fields").append(row);
  }
});
