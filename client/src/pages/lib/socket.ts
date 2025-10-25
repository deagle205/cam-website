import { io } from "socket.io-client";

const protocol = import.meta.env.VITE_MODE === "production" ? "https" : "http";
const host = import.meta.env.VITE_SERVER_IP;
const port = import.meta.env.VITE_SERVER_PORT;

const socket = io(`${protocol}://${host}:${port}`, {
  autoConnect: false,
  auth: (cb) => cb({ accessToken: localStorage.getItem("accessToken") ?? "" }),
});

export default socket;
