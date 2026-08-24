// Compatibility entrypoint for the acceptance command executed from the app root.
// The canonical configuration and all browser dependencies remain frontend-owned.
import frontendConfig from "./frontend/playwright.config";

export default { ...frontendConfig, testDir: "./frontend/e2e" };
