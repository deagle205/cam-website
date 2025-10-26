export default function apiKeyAuth(req, res, next) {
  const providedKey = req.header("x-api-key"); // standard header name
  const validKeys = process.env.API_KEYS?.split(",") || [];

  if (!providedKey || !validKeys.includes(providedKey)) {
    return res.status(403).json({ error: "Forbidden: Invalid API key" });
  }

  next();
}