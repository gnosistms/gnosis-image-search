# PyInstaller's bytecode optimizer drops the comprehension's leaked `name`
# binding that this PyTorch module intentionally uses later at module scope.
# Collect the original source for this one module so Python executes it with
# standard language semantics inside the frozen backend.
module_collection_mode = "py"
