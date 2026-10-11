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
    const hasValues = event.target.form.dataset.card === "true";
    const input = (name, attributes = {}) => {
      const element = document.createElement("input");
      element.name = name;
      Object.assign(element, attributes);
      return element;
    };
    row.append(
      input("field_key", {type: "hidden", value: ""}),
      input("field_label", {required: true, placeholder: "Label"}),
    );
    const type = document.createElement("select");
    type.name = "field_type";
    ["text", "number", "boolean", "date", "select", "text[]"].forEach((value) => {
      const option = document.createElement("option");
      option.textContent = value;
      type.append(option);
    });
    row.append(type, input("field_options", {placeholder: "Select options, comma separated"}));
    if (hasValues) {
      row.append(input("field_value", {placeholder: "Value"}), input("field_present", {type: "hidden", value: "0"}));
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-row";
    remove.textContent = "Remove";
    row.append(remove);
    document.querySelector("#fields").append(row);
  }
});
