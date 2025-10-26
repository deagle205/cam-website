import express from "express";
import mongoose from "mongoose";
import dotenv from "dotenv";
import DiningData from "./models/DiningData.js";
import Traffic from "./models/Traffic.js";
import apiKeyAuth from "./models/middleware/apiKeyAuth.js";

dotenv.config();

const app = express();
app.use(express.json());

// connect to MongoDB Atlas
mongoose.connect(process.env.MONGO_CONNECTION_STRING, {
  useNewUrlParser: true,
  useUnifiedTopology: true
}).then(() => console.log("Connected to MongoDB Atlas"))
  .catch(err => console.error("MongoDB connection error:", err));

// example routes
app.get("/traffic", async (req, res) => {
  try {
    const data = await Traffic.find()
      .sort({ timestamp: -1 })
      .limit(50);
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/traffic", apiKeyAuth, async (req, res) => {
  try {
    const newEntry = new Traffic(req.body);
    await newEntry.save();

    const data = await Traffic.find()
      .sort({ timestamp: -1 })
      .limit(50);

    res.status(201).json(data);
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});


const PORT = process.env.PORT || 5000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));