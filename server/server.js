import express from "express";
import { createServer } from "node:http";
import { Server } from "socket.io";
import cors from "cors";
import mongoose from "mongoose";
import User from "./models/User.js";
import RefreshToken from "./models/RefreshToken.js";
import dotenv from "dotenv";
import jwt from "jsonwebtoken";
import ms from "ms";
import cookieParser from "cookie-parser";
import bcrypt from "bcrypt";
import { UAParser } from "ua-parser-js";
import { rateLimit } from "express-rate-limit";

dotenv.config();
const app = express();
const server = createServer(app);
const port = parseInt(process.env.SERVER_PORT);

const limiter = rateLimit({
  windowMs: ms("15m"),
  limit: 100,
  standardHeaders: "draft-8",
  legacyHeaders: false,
});

try {
  await mongoose.connect(process.env.MONGO_CONNECTION_STRING);
  console.log("Connected to MongoDB");
} catch (err) {
  console.error(err);
}

app.use(limiter);
app.use(
  cors({
    origin:
      process.env.NODE_ENV === "production"
        ? ["https://full-stack-messenger.vercel.app"]
        : ["http://localhost:5173", "http://192.168.1.30:5173"],
    methods: ["GET", "POST", "DELETE"],
    credentials: true,
  })
);
app.use(express.json());
app.use(cookieParser());

const authenticateToken = (req, res, next) => {
  const authHeader = req.headers["authorization"];
  const token = authHeader && authHeader.split(" ")[1];
  if (!token) {
    return res.sendStatus(401);
  }

  jwt.verify(token, process.env.ACCESS_TOKEN_SECRET, (err, userInfo) => {
    if (err) return res.sendStatus(403);
    req.userInfo = userInfo;
    next();
  });
};

const generateAccessToken = (userInfo) => {
  return jwt.sign(userInfo, process.env.ACCESS_TOKEN_SECRET, {
    expiresIn: "5m",
  });
};

app.post("/api/login", async (req, res) => {
  const { username, password } = req.body;
  try {
    const user = await User.findOne({ username });

    if (!user || !(await user.isValidPassword(password))) {
      return res.status(401).json({ error: "Invalid username or password" });
    }
    const userId = user._id;
    const accessToken = generateAccessToken({ username, userId });
    const { browser, cpu, os } = UAParser(req.headers["user-agent"]);
    const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress;
    const time = new Date();
    const formattedTime = time.toLocaleString();
    const refreshTokenMetadata =
      `${browser.name}, ${os.name} ` +
      `${cpu.architecture} from ${ip} at ${formattedTime}`;
    const refreshToken = jwt.sign(
      { username, userId },
      process.env.REFRESH_TOKEN_SECRET
    );
    await RefreshToken.create({
      userId,
      refreshTokenMetadata,
      tokenHash: refreshToken,
      timestamp: time.getTime(),
      expiryDate: time.getTime() + ms("2w"),
    });

    res.cookie("refreshToken", refreshToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: process.env.NODE_ENV === "production" ? "none" : "strict",
      maxAge: ms("2w"),
    });
    res.status(200).json({ accessToken, username, userId });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "Server error" });
  }
});

app.delete("/api/logout", async (req, res) => {
  const refreshToken = req.cookies.refreshToken;
  if (!refreshToken) return res.sendStatus(204);
  try {
    const decoded = jwt.verify(refreshToken, process.env.REFRESH_TOKEN_SECRET);
    await RefreshToken.findOneAndDelete({ userId: decoded.userId });
  } catch (err) {
    return res.status(401).json({ error: "Refresh token invalid" });
  }
  res.clearCookie("refreshToken");
  res.sendStatus(204);
});

app.post("/api/refresh", async (req, res) => {
  const refreshToken = req.cookies.refreshToken;
  if (!refreshToken) return res.status(401).json({ error: "No refresh token" });
  try {
    const decoded = jwt.verify(refreshToken, process.env.REFRESH_TOKEN_SECRET);
    const token = await RefreshToken.findOne({ userId: decoded.userId });
    if (!bcrypt.compare(refreshToken, token.tokenHash))
      return res.status(404).json({ error: "Token not found" });
    if (token.expiryDate < Date.now()) {
      RefreshToken.deleteOne({ tokenHash: token.tokenHash });
      res.clearCookie("refreshToken");
      return res.status(401).json({ error: "Token has expired" });
    }
    const accessToken = generateAccessToken({
      username: decoded.username,
      userId: decoded.userId,
    });
    res.status(200).json({
      accessToken,
      username: decoded.username,
      userId: decoded.userId,
    });
  } catch (err) {
    console.error(err);
    res.status(403).json({ error: "Invalid token" });
  }
});

app.post("/api/create-user", authenticateToken, async (req, res) => {
  const { username, passwordHash } = req.body;

  if (!username || typeof username !== "string") {
    return res.status(400).json({ error: "Invalid username" });
  }

  const exists = await User.findOne({ username });
  if (exists) {
    return res.status(409).json({ error: "Username taken" });
  }

  if (!passwordHash || typeof passwordHash !== "string") {
    return res.status(400).json({ error: "Invalid password" });
  }

  if (passwordHash.length < 8) {
    return res
      .status(400)
      .json({ error: "Password too short (<8 characters)" });
  }

  await User.create({ username, passwordHash });
  return res.status(201).json({ message: "User created" });
});

const io = new Server(server, {
  cors: {
    origin:
      process.env.NODE_ENV === "production"
        ? ["https://full-stack-messenger.vercel.app"]
        : ["http://localhost:5173", "http://192.168.1.30:5173"],
    methods: ["GET", "POST", "DELETE"],
  },
});

io.use((socket, next) => {
  const token = socket.handshake.auth.accessToken;
  if (!token) return next(new Error("Error: No token provided"));
  jwt.verify(token, process.env.ACCESS_TOKEN_SECRET, (err, userInfo) => {
    if (err) return next(new Error("Access token expired or invalid"));
    socket.userInfo = userInfo;
    next();
  });
});

io.on("connection", async (socket) => {
  console.log(
    `User "${socket.userInfo.username} ${socket.userInfo.userId}" has connected from the socket "${socket.id}"`
  );
  socket.emit("user-info", socket.userInfo.username, socket.userInfo.userId);

  socket.on(
    "join-user-room",
    async () => await socket.join(socket.userInfo.userId)
  );

  socket.on(
    "leave-user-room",
    async () => await socket.leave(socket.userInfo.userId)
  );

  socket.on(
    "join-conversation",
    async (conversationId) => await socket.join(conversationId)
  );

  socket.on("validate-usernames", async (usernameArray, callback) => {
    const len = usernameArray.length;
    const userIdArray = [];
    for (let i = 0; i < len; i++) {
      const user = await User.findOne({ username: usernameArray[i] });
      if (!user) {
        return callback(usernameArray[i]);
      }
      userIdArray.push(user._id.toString());
    }
    callback(userIdArray);
  });

  socket.on(
    "send-message",
    async (sender, mentions, text, conversationId, messageSent) => {
      try {
        const time = Date.now();
        const msg = await Message.create({
          sender,
          mentions,
          text,
          conversationId,
          timestamp: time,
        });
        console.log(`Saved message "${msg.id}"`);
        io.to(conversationId).emit("receive-message", msg);
        console.log(`Sent message "${msg.id}"`);
        const conversation = await Conversation.findByIdAndUpdate(
          conversationId,
          { lastUpdated: time, lastUser: sender, lastMessage: text }
        );
        io.to(conversation.members).emit(
          "receive-conversation-update",
          conversationId,
          sender,
          text
        );
        console.log(`Sent conversation update "${conversationId}"`);
        messageSent(true);
      } catch (err) {
        console.error(err);
        messageSent(false);
      }
    }
  );

  socket.on(
    "create-conversation",
    async (conversationName, isDM, members, conversationCreated) => {
      try {
        members.push(socket.userInfo.userId);
        const time = Date.now();
        const conversation = await Conversation.create({
          conversationName,
          isDM,
          members,
          lastUpdated: time,
          createTime: time,
        });
        console.log(`Saved conversation "${conversation.id}"`);
        for (const userId of members) {
          console.log(`Sending to user ${userId}...`);
          io.to(userId).emit("receive-conversation", conversation);
        }
        console.log(`Sent conversation "${conversation.id}"`);
        conversationCreated(true);
      } catch (err) {
        console.error(err);
        conversationCreated(false);
      }
    }
  );

  socket.on(
    "change-password",
    async (currentPassword, newPassword, success) => {
      const userId = socket.userInfo.userId;
      try {
        const user = await User.findById(userId);
        if (await user.isValidPassword(currentPassword)) {
          user.passwordHash = newPassword;
          await user.save();
          success(true);
        } else {
          console.error("Invalid current password");
          success(false);
        }
      } catch (err) {
        console.error(err);
        success(false);
      }
    }
  );

  socket.on("disconnect", () => {
    console.log(
      `User "${socket.userInfo.username} ${socket.userInfo.userId}" disconnected from "${socket.id}`
    );
  });
});

server.listen(port, () => {
  console.log(`Server running on port ${port}`);
  server.address;
});
