import { io } from "socket.io-client";

export const socket = io("/", {
  withCredentials: true,
  autoConnect: true,
  transports: ["websocket", "polling"],
});
