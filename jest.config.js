/** Jest config for browser-side static asset tests. */
module.exports = {
  rootDir: __dirname,
  testEnvironment: "jsdom",
  testMatch: ["<rootDir>/tests/js/**/*.test.js"],
};
