from highrise import BaseBot, User, Position, AnchorPosition
from highrise.models import SessionMetadata, Item
from highrise.webapi import WebAPI
from highrise.__main__ import main, arun, BotDefinition
from flask import Flask
from threading import Thread
import time
import random
from importlib import import_module
import json
import asyncio
import os
import requests
from datetime import datetime

def split_message(text, max_length=200):
    """Split long messages into smaller chunks"""
    lines = text.split('\n')
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_length:
            chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line

    if current:
        chunks.append(current)

    return chunks

class Mybot(BaseBot):
    def __init__(self) -> None:
        super().__init__()
        self.load_game_config()
        self.balances = self.load_balances()
        self.credits = self.load_credits()
        self.owner_usernames = ["TITOMOSTAFA", ""] 
        self.my_user_id = None
        self.user_stats = self.load_user_stats()
        self.daily_rewards = self.load_daily_rewards()
        self.allowed_whispers = self.load_allowed_whispers()
        self.vips = self.load_vips()
        self.bot_start_time = datetime.now()
        self.users_messaged_bot = self.load_users_messaged_bot()  # Track users who messaged the bot
        self.user_conversations = self.load_user_conversations()  # Map user_id -> conversation_id for sending invites
        # Initialize room_id to None - will be set from environment or session_metadata in on_start
        self.room_id = None
        self.current_room_id = None
        self.invited_users = self.load_invited_users()  # Track users who have been sent an invite
        
        # Guess Face Game state (active session state)
        self.guess_face_game.update({
            "active": False,
            "round": 0,
            "players": [],
            "chosen_player": None,
            "secret_word": None,
            "revealed_letters": set(),
            "votes": {},
            "phase": None,
            "discussion_ready": set(),  # Players who are ready to move to danger zone
            "game_ending": False,  # Flag to prevent new game starts during cleanup
            "prize_active": False,
            "prize_amount": 0,
            "min_players": 0,
            "starting": False,
            "chooser_timeout_task": None,  # Task for chooser timeout
            "chooser_timeout_time": None  # When the chooser timeout started
        })

        try:
            self.webapi = WebAPI()
            print("WebAPI initialized successfully")
        except Exception as e:
            print(f"WebAPI initialization failed: {e}")
            self.webapi = None  # type: ignore

    def save_game_config(self):
        config = {
            "saved_position": {
                "x": self.saved_position.x,  # type: ignore
                "y": self.saved_position.y,  # type: ignore
                "z": self.saved_position.z,  # type: ignore
                "facing": self.saved_position.facing  # type: ignore
            } if self.saved_position else None,
            "down_position": {
                "x": self.down_position.x,  # type: ignore
                "y": self.down_position.y,  # type: ignore
                "z": self.down_position.z,  # type: ignore
                "facing": self.down_position.facing  # type: ignore
            } if self.down_position else None,
            "blocks": self.guess_face_game.get("blocks", {}),
            "rows": self.guess_face_game.get("rows", {}),
            "rows_config": self.guess_face_game.get("rows_config", {}),
            "chooser_pos": self.guess_face_game.get("chooser_pos"),
            "danger_pos": self.guess_face_game.get("danger_pos"),
            "spawn_pos": self.guess_face_game.get("spawn_pos"),
            "exit_pos": self.guess_face_game.get("exit_pos"),
            "vip_pos": self.guess_face_game.get("vip_pos"),
            "host_pos": self.guess_face_game.get("host_pos"),
            "sit_pos": self.guess_face_game.get("sit_pos")
        }
        with open("game_config.json", "w") as f:
            json.dump(config, f)

    def load_game_config(self):
        self.saved_position = None
        self.down_position = None
        self.guess_face_game = {
            "blocks": {},
            "rows": {},
            "rows_config": {},
            "chooser_pos": None,
            "danger_pos": None,
            "spawn_pos": None,
            "exit_pos": None,
            "vip_pos": None,
            "host_pos": None,
            "sit_pos": None,
            "player_positions": {},  # Track which block each player is on
            "danger_zone_players": [],  # Players currently in danger zone (up to 5)
            "excluded_players": set(),  # Players who guessed wrong and are excluded
            "frozen_players": set(),  # Players who stay on their block (no danger zone)
            "left_during_waiting": set()  # Players who left during waiting phase
        }
        try:
            with open("game_config.json", "r") as f:
                config = json.load(f)
                pos = config.get("saved_position")
                if pos:
                    self.saved_position = Position(pos['x'], pos['y'], pos['z'], pos['facing'])
                down_pos = config.get("down_position")
                if down_pos:
                    self.down_position = Position(down_pos['x'], down_pos['y'], down_pos['z'], down_pos['facing'])
                self.guess_face_game["blocks"] = config.get("blocks", {})
                self.guess_face_game["rows"] = config.get("rows", {})
                self.guess_face_game["rows_config"] = config.get("rows_config", {})
                self.guess_face_game["chooser_pos"] = config.get("chooser_pos")
                self.guess_face_game["danger_pos"] = config.get("danger_pos")
                self.guess_face_game["spawn_pos"] = config.get("spawn_pos")
                self.guess_face_game["exit_pos"] = config.get("exit_pos")
                self.guess_face_game["vip_pos"] = config.get("vip_pos")
                self.guess_face_game["host_pos"] = config.get("host_pos")
                self.guess_face_game["sit_pos"] = config.get("sit_pos")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        self.my_user_id = session_metadata.user_id
        # Dynamically get the room ID the bot is currently in from session_metadata
        # Check if room_info exists and has room_id, otherwise fallback
        self.room_id = os.getenv("HIGHRISE_ROOM_ID", "665339cebb0667c76e14c27d")
        if hasattr(session_metadata, "room_info") and session_metadata.room_info:
            # Try different possible attribute names for room ID
            self.room_id = getattr(session_metadata.room_info, "room_id", 
                                  getattr(session_metadata.room_info, "id", self.room_id))
        
        # Automatically update current_room_id to the room the bot is in
        self.current_room_id = self.room_id
        self.save_room_id()
            
        print(f"Bot started! User ID: {self.my_user_id} in Room ID: {self.room_id}")
        
        # Send welcome message about invite system
        await self.highrise.chat("🎮 Welcome to Guess Face Bot!\n💌 Want a room invite? Send me a private message!")
        
        asyncio.create_task(self.save_data_periodically())

    async def on_user_join(self, user: User, position: Position | AnchorPosition) -> None:
        # Track user who joined the room for !invite command
        self.users_messaged_bot.add(user.id)
        self.save_users_messaged_bot()
        print(f"👤 Added {user.username} (ID: {user.id}) to users_messaged_bot. Total: {len(self.users_messaged_bot)}")
        
        if user.username not in self.balances:
            self.balances[user.username] = 1000
            self.save_balances()
        if user.username not in self.credits:
            self.credits[user.username] = 0
            self.save_credits()
        if user.username not in self.user_stats:
            self.user_stats[user.username] = {
                "games_played": 0,
                "games_won": 0,
                "games_lost": 0,
                "total_wagered": 0,
                "total_won": 0,
                "biggest_win": 0,
                "join_date": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat()
            }
            self.save_user_stats()

        try:
            await asyncio.sleep(1.0) # Small delay to ensure user is fully in the room
            
            # Send welcome message without invite link
            welcome_msg = (
                f"🎮 Welcome @{user.username}!\n"
                "📋 Commands: type 'commands' or 'help' in private message"
            )
            await self.highrise.send_whisper(user.id, welcome_msg)
            print(f"✅ Welcome message sent to {user.username}")
            
        except Exception as e:
            if "left the room" in str(e).lower():
                print(f"ℹ️ User {user.username} left before welcome whisper could be sent.")
            else:
                print(f"❌ Welcome whisper error for {user.username}: {e}")

    async def on_user_leave(self, user: User) -> None:
        if user.username in self.user_stats:
            self.user_stats[user.username]["last_seen"] = datetime.now().isoformat()
            self.save_user_stats()

        # Remove from game if active
        if self.guess_face_game["active"] and user.username in self.guess_face_game["players"]:
            self.guess_face_game["players"].remove(user.username)
            await self.highrise.chat(f"{user.username} left the game")


    async def on_chat(self, user: User, message: str) -> None:  # type: ignore
        username = user.username

        if username not in self.balances:
            self.balances[username] = 1000
            self.save_balances()
        if username not in self.credits:
            self.credits[username] = 0
            self.save_credits()

        msg = message.lower().strip()
        
        # Set room command - update the room ID for invites
        if msg.startswith("!setroom") or msg.startswith("setroom"):
            if not await self.is_owner(user):
                await self.highrise.chat("❌ Only owner can use this command!")
                return
            
            parts = msg.split()
            if len(parts) < 2:
                await self.highrise.send_whisper(user.id, f"📍 Current room ID: {self.current_room_id}\n💡 Usage: !setroom <room_id>")
                return
            
            new_room_id = parts[1]
            self.current_room_id = new_room_id
            self.save_room_id()
            await self.highrise.send_whisper(user.id, f"✅ Room updated! New invites will be sent to: {new_room_id}")
            return
        
        # Invite command - send room invites to all users who messaged the bot
        if msg == "!invite" or msg == "invite":
            if not await self.is_owner(user):
                await self.highrise.chat("❌ Only owner can use this command!")
                return
            
            if not self.users_messaged_bot:
                await self.highrise.chat("📭 No users have messaged the bot yet.")
                return
            
            sent_count = 0
            fail_count = 0
            
            await self.highrise.chat(f"📢 Starting to send invites to {len(self.users_messaged_bot)} users...")
            
            for user_id in list(self.users_messaged_bot):
                try:
                    # Try to send invite to each user
                    dummy_user = User(id=user_id, username="User")  # type: ignore
                    conv_id = self.user_conversations.get(user_id)
                    
                    # We MUST have a conversation ID to send a message/invite
                    if not conv_id:
                        fail_count += 1
                        continue
                        
                    await self.send_invite_to_room(dummy_user, conv_id)
                    sent_count += 1
                    # Small delay to prevent rate limiting if many users
                    if sent_count % 5 == 0:
                        await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"Error sending invite to {user_id}: {e}")
                    fail_count += 1
            
            result_msg = f"📬 Sent {sent_count} invites!"
            if fail_count > 0:
                result_msg += f" ({fail_count} failed - users need to DM bot first)"
            
            await self.highrise.chat(result_msg)
            return
        
        # Check if message is a city name (vote in public chat)
        allowed_words = ["berlin", "reykjavik", "new york", "london", "moscow", "paris"]
        if msg in allowed_words and self.guess_face_game.get("phase") in ["voting", "discussion"]:
            # Player is voting via public chat
            await self.handle_vote_command(user, msg)
            return

        if msg == "save":
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            room_users = (await self.highrise.get_room_users()).content  # type: ignore
            for u, pos in room_users:
                if u.id == user.id:
                    self.saved_position = pos
                    self.save_game_config()
                    await self.highrise.chat("Position saved successfully!")
                    return
            await self.highrise.chat("Could not find your position.")
            return

        if msg == "go":
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            if self.saved_position:
                await self.highrise.walk_to(self.saved_position)
                await self.highrise.chat("Going to saved position...")
            else:
                await self.highrise.chat("No saved position found.")
            return

        if msg == "save down":
            if not await self.is_vip(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for moderators/VIP only!")
                return
            room_users = (await self.highrise.get_room_users()).content  # type: ignore
            for u, pos in room_users:
                if u.id == user.id:
                    self.down_position = pos
                    self.save_game_config()
                    await self.highrise.chat(f"✅ {username} saved the down position!")
                    return
            await self.highrise.chat("Could not find your position.")
            return

        if msg == "down":
            if not await self.is_vip(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for moderators/VIP only!")
                return
            if self.down_position:
                try:
                    room_users_resp = await self.highrise.get_room_users()
                    room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                    for u, pos in room_users:
                        if u.id == user.id:
                            await self.highrise.teleport(u.id, self.down_position)  # type: ignore
                            await self.highrise.chat(f"⬇️ {username} went down!")
                            return
                except Exception as e:
                    await self.highrise.send_whisper(user.id, f"Error teleporting: {str(e)}")
            else:
                await self.highrise.chat("No down position saved. Use 'save down' first.")
            return

        if msg.startswith("set row"):
            if not await self.is_owner(user):
                return
            try:
                parts = msg.split(" ")
                if len(parts) < 3:
                    await self.highrise.chat("Usage: set row [row number] [number of blocks]")
                    return
                
                row_num = int(parts[2])
                num_blocks = int(parts[3]) if len(parts) > 3 else 15
                
                if row_num < 1:
                    await self.highrise.chat("❌ Row number must be 1 or greater")
                    return
                
                room_users = (await self.highrise.get_room_users()).content  # type: ignore
                found = False
                for u, pos in room_users:
                    if u.id == user.id:
                        found = True
                        if "rows" not in self.guess_face_game: self.guess_face_game["rows"] = {}
                        row_key = str(row_num - 1)
                        blocks = []
                        for i in range(num_blocks):
                            blocks.append({"x": pos.x + (i * 2.0), "y": pos.y, "z": pos.z, "facing": pos.facing})  # type: ignore
                        
                        self.guess_face_game["rows"][row_key] = {"blocks": blocks, "num_blocks": num_blocks}
                        self.save_game_config()
                        await self.highrise.chat(f"✅ Row saved {row_num} successfully!")
                        return
                
                if not found:
                    await self.highrise.chat("❌ Could not find your position.")
            except:
                await self.highrise.chat("❌ Input error.")
            return

        if msg.startswith("set chooser"):
            if not await self.is_owner(user):
                return
            try:
                parts = msg.split(" ")
                num_blocks = int(parts[2]) if len(parts) > 2 else 5
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.id == user.id:
                        blocks = []
                        block_spacing = 2.0
                        for i in range(num_blocks):
                            blocks.append({
                                "x": pos.x + (i * block_spacing),  # type: ignore
                                "y": pos.y,  # type: ignore
                                "z": pos.z,  # type: ignore
                                "facing": pos.facing  # type: ignore
                            })
                        self.guess_face_game["chooser_pos"] = {"blocks": blocks, "num_blocks": num_blocks}
                        self.save_game_config()
                        await self.highrise.chat(f"✅ Chooser area saved! ({num_blocks} blocks)")
                        await self.highrise.send_whisper(user.id, f"📍 Chooser area saved at:\nX: {pos.x:.1f}\nBlocks: {num_blocks}")  # type: ignore
                        return
            except (ValueError, IndexError):
                await self.highrise.chat("❌ Usage: set chooser [number of Blocks optional - default 5]")
            return

        if msg.startswith("set danger"):
            if not await self.is_owner(user):
                return
            try:
                parts = msg.split(" ")
                
                # Support multiple formats:
                # "set danger1 [blocks]" - row 1
                # "set danger2 [blocks]" - row 2
                # "set danger row 1 [blocks]" - row 1
                # "set danger row 2 [blocks]" - row 2
                # "set danger [blocks]" - defaults to row 1
                
                row_num = 1
                num_blocks = 5
                
                if msg.startswith("set danger1"):
                    row_num = 1
                    num_blocks = int(parts[2]) if len(parts) > 2 else 5
                elif msg.startswith("set danger2"):
                    row_num = 2
                    num_blocks = int(parts[2]) if len(parts) > 2 else 5
                elif len(parts) >= 3 and parts[2] == "row":
                    # "set danger row 1 [blocks]" format
                    row_num = int(parts[3])
                    num_blocks = int(parts[4]) if len(parts) > 4 else 5
                else:
                    # "set danger [blocks]" format - defaults to row 1
                    num_blocks = int(parts[1]) if len(parts) > 1 else 5
                
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.id == user.id:
                        blocks = []
                        block_spacing = 2.0
                        for i in range(num_blocks):
                            blocks.append({
                                "x": pos.x + (i * block_spacing),  # type: ignore
                                "y": pos.y,  # type: ignore
                                "z": pos.z,  # type: ignore
                                "facing": pos.facing  # type: ignore
                            })
                        
                        # Initialize danger_pos as rows if it doesn't exist
                        if "danger_pos" not in self.guess_face_game:
                            self.guess_face_game["danger_pos"] = {"rows": {}}
                        
                        # Save to the specified row
                        if "rows" not in self.guess_face_game["danger_pos"]:
                            self.guess_face_game["danger_pos"]["rows"] = {}
                        
                        self.guess_face_game["danger_pos"]["rows"][str(row_num)] = {
                            "blocks": blocks,
                            "num_blocks": num_blocks
                        }
                        
                        self.save_game_config()
                        await self.highrise.chat(f"✅ Danger zone saved for row #{row_num}! ({num_blocks} blocks)")
                        await self.highrise.send_whisper(user.id, f"📍 Danger zone saved for row {row_num} at:\nX: {pos.x:.1f}\nBlocks: {num_blocks}")  # type: ignore
                        return
            except (ValueError, IndexError):
                await self.highrise.chat("❌ Usage: set danger1 [blocks] or set danger2 [blocks] or set danger row [1/2] [blocks]")
            return

        if msg.startswith("set spawn"):
            if not await self.is_owner(user):
                return
            try:
                parts = msg.split(" ")
                num_blocks = int(parts[2]) if len(parts) > 2 else 5
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.id == user.id:
                        blocks = []
                        block_spacing = 2.0
                        for i in range(num_blocks):
                            blocks.append({
                                "x": pos.x + (i * block_spacing),  # type: ignore
                                "y": pos.y,  # type: ignore
                                "z": pos.z,  # type: ignore
                                "facing": pos.facing  # type: ignore
                            })
                        self.guess_face_game["spawn_pos"] = {"blocks": blocks, "num_blocks": num_blocks}
                        self.save_game_config()
                        await self.highrise.chat(f"✅ Spawn area saved! ({num_blocks} blocks)")
                        await self.highrise.send_whisper(user.id, f"📍 Spawn area saved at:\nX: {pos.x:.1f}\nBlocks: {num_blocks}")  # type: ignore
                        return
            except (ValueError, IndexError):
                await self.highrise.chat("❌ Usage: set spawn [number of Blocks optional - default 5]")
            return

        if msg.startswith("set exit"):
            if not await self.is_owner(user):
                return
            try:
                parts = msg.split(" ")
                num_blocks = int(parts[2]) if len(parts) > 2 else 5
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.id == user.id:
                        blocks = []
                        block_spacing = 2.0
                        for i in range(num_blocks):
                            blocks.append({
                                "x": pos.x + (i * block_spacing),  # type: ignore
                                "y": pos.y,  # type: ignore
                                "z": pos.z,  # type: ignore
                                "facing": pos.facing  # type: ignore
                            })
                        self.guess_face_game["exit_pos"] = {"blocks": blocks, "num_blocks": num_blocks}
                        self.save_game_config()
                        await self.highrise.chat(f"✅ Exit area saved! ({num_blocks} blocks)")
                        await self.highrise.send_whisper(user.id, f"📍 Exit area saved at:\nX: {pos.x:.1f}\nBlocks: {num_blocks}")  # type: ignore
                        return
            except (ValueError, IndexError):
                await self.highrise.chat("❌ Usage: set exit [number of Blocks optional - default 5]")
            return

        if msg.startswith("set vip_spot"):
            if not await self.is_vip(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for VIP only!")
                return
            try:
                parts = msg.split(" ")
                num_blocks = int(parts[2]) if len(parts) > 2 else 5
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.id == user.id:
                        blocks = []
                        block_spacing = 2.0
                        for i in range(num_blocks):
                            blocks.append({
                                "x": pos.x + (i * block_spacing),  # type: ignore
                                "y": pos.y,  # type: ignore
                                "z": pos.z,  # type: ignore
                                "facing": pos.facing  # type: ignore
                            })
                        self.guess_face_game["vip_pos"] = {"blocks": blocks, "num_blocks": num_blocks}
                        self.save_game_config()
                        await self.highrise.chat(f"⭐ VIP area saved! ({num_blocks} blocks)")
                        await self.highrise.send_whisper(user.id, f"📍 VIP area saved at:\nX: {pos.x:.1f}\nBlocks: {num_blocks}")  # type: ignore
                        return
            except (ValueError, IndexError):
                await self.highrise.chat("❌ Usage: set vip_spot [optional blocks - default 5]")
            return

        if msg.startswith("set host"):
            if not await self.is_vip(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for moderators/VIP only!")
                return
            try:
                parts = msg.split(" ")
                num_blocks = int(parts[2]) if len(parts) > 2 else 5
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.id == user.id:
                        blocks = []
                        block_spacing = 2.0
                        for i in range(num_blocks):
                            blocks.append({
                                "x": pos.x + (i * block_spacing),  # type: ignore
                                "y": pos.y,  # type: ignore
                                "z": pos.z,  # type: ignore
                                "facing": pos.facing  # type: ignore
                            })
                        self.guess_face_game["host_pos"] = {"blocks": blocks, "num_blocks": num_blocks}
                        self.save_game_config()
                        await self.highrise.chat(f"👑 Host area saved! ({num_blocks} blocks)")
                        await self.highrise.send_whisper(user.id, f"📍 Host area saved at:\nX: {pos.x:.1f}\nBlocks: {num_blocks}")  # type: ignore
                        return
            except (ValueError, IndexError):
                await self.highrise.chat("❌ Usage: set host [optional blocks - default 5]")
            return

        if msg == "rows":
            if not await self.is_owner(user):
                return
            rows = self.guess_face_game.get("rows", {})
            if not rows:
                await self.highrise.chat("No rows saved! Use 'set row [number] [Blocks]' to save them.")
                return
            
            # Send rows in small chunks to avoid "Message too long" error
            await self.highrise.send_whisper(user.id, "📊 Saved rows:")
            await asyncio.sleep(0.5)
            rows_list = sorted(rows.keys(), key=lambda x: int(x) if isinstance(x, int) or x.isdigit() else 0)
            
            for row_id in rows_list:
                r = rows[row_id]
                row_num = int(row_id) + 1
                
                if "blocks" in r:
                    num_blocks = r.get("num_blocks", len(r["blocks"]))
                    first_block = r["blocks"][0]
                    msg_text = f"Row {row_num}: {num_blocks} blocks starting at X={first_block['x']:.1f} Z={first_block['z']:.1f}"
                else:
                    msg_text = f"Row {row_num}: X={r.get('x', 0):.1f} Y={r.get('y', 0):.1f} Z={r.get('z', 0):.1f}"
                
                try:
                    await self.highrise.send_whisper(user.id, msg_text)
                    await asyncio.sleep(0.2)
                except:
                    pass
            return

        if msg == "config":
            if not await self.is_owner(user):
                return
            config_info = """⚙️ Current game settings:

📊 Rows: {'✅ Setup' if self.guess_face_game.get('rows') else '❌ Not configured'}
📍 Chooser area: {'✅ Setup' if self.guess_face_game.get('chooser_pos') else '❌ Not configured'}
⚡ Danger zone: {'✅ Setup' if self.guess_face_game.get('danger_pos') else '❌ Not configured'}
🚪 Spawn/Entry: {'✅ Setup' if self.guess_face_game.get('spawn_pos') else '❌ Not configured'}
🚪 Exit area: {'✅ Setup' if self.guess_face_game.get('exit_pos') else '❌ Not configured'}

Setup commands (owner only):
• set row [number] [blocks] - save row
• rows - display all saved rows
• set chooser [blocks] - set chooser area
• set danger [blocks] - set danger zone
• set spawn [blocks] - set spawn location
• set exit [blocks] - set exit location
• set chair - set bot sitting position
• sit - make bot sit
• !end - end the game
• kick @username - kick a player from the game
• !change chooser @username - change the chooser"""
            await self.highrise.send_whisper(user.id, config_info)
            return

        if msg == "!end":
            if not await self.is_vip(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for VIP/moderators only!")
                return
            
            spawn_pos = self.guess_face_game.get("spawn_pos")
            if not spawn_pos:
                await self.highrise.send_whisper(user.id, "❌ Spawn position not set! Please use 'set spawn' command first.")
                return
            
            try:
                # Reset game state
                self.guess_face_game["active"] = False
                self.guess_face_game["players"] = []
                self.guess_face_game["phase"] = None
                self.guess_face_game["game_ending"] = False
                self.guess_face_game["chosen_player"] = None
                self.guess_face_game["secret_word"] = None
                self.guess_face_game["excluded_players"] = set()
                self.guess_face_game["frozen_players"] = set()
                
                # Teleport all players in the room to spawn
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                
                teleport_pos = spawn_pos
                if "blocks" in spawn_pos and spawn_pos["blocks"]:
                    teleport_pos = spawn_pos["blocks"][0]
                
                await self.highrise.chat("🏁 Game ended! Moving all players to spawn...")
                
                for u, pos in room_users:
                    # Skip the bot itself
                    if u.id == self.my_user_id:
                        continue
                    await self.highrise.teleport(u.id, Position(teleport_pos['x'], teleport_pos['y'], teleport_pos['z'], teleport_pos['facing']))  # type: ignore
                    await asyncio.sleep(0.1) # Avoid rate limits
                
                return
            except Exception as e:
                await self.highrise.send_whisper(user.id, f"❌ Error ending game: {str(e)}")
            return

        if msg == "set chair":
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            try:
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.id == user.id:
                        # Try to save position with available attributes
                        try:
                            # Try Position type first (x, y, z attributes)
                            if hasattr(pos, 'x') and hasattr(pos, 'y') and hasattr(pos, 'z'):
                                self.guess_face_game["sit_pos"] = {
                                    "x": pos.x,  # type: ignore
                                    "y": pos.y,  # type: ignore
                                    "z": pos.z,  # type: ignore
                                    "facing": getattr(pos, 'facing', 'ForwardDown'),
                                    "type": "position"
                                }
                                self.save_game_config()
                                await self.highrise.chat(f"✅ Chair position saved! ({username} is now the chair)")
                                await self.highrise.send_whisper(user.id, "📍 Your chair position saved!\nType 'sit' to make me sit here")
                                return
                            # Try AnchorPosition type (entity_id and anchor_ix)
                            elif hasattr(pos, 'entity_id') and hasattr(pos, 'anchor_ix'):
                                self.guess_face_game["sit_pos"] = {
                                    "entity_id": str(pos.entity_id),  # type: ignore
                                    "anchor_ix": int(pos.anchor_ix),  # type: ignore
                                    "type": "anchor"
                                }
                                self.save_game_config()
                                await self.highrise.chat(f"✅ Chair position saved! ({username} is now the chair)")
                                await self.highrise.send_whisper(user.id, "📍 Your chair position saved!\nType 'sit' to make me sit here")
                                return
                            else:
                                # Debug: show what attributes the position has
                                attrs = dir(pos)
                                await self.highrise.chat("❌ Could not identify position type. Try standing somewhere else!")
                                return
                        except Exception as e:
                            await self.highrise.chat(f"❌ Error with position: {str(e)}")
                            return
            except Exception as e:
                await self.highrise.chat(f"❌ Error saving chair position: {str(e)}")
            return
        
        if msg == "sit":
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            
            sit_pos = self.guess_face_game.get("sit_pos")
            if not sit_pos:
                await self.highrise.chat("❌ Chair position not set! Use 'set chair' first.")
                return
            
            try:
                # Handle both Position and AnchorPosition
                if sit_pos.get("type") == "anchor":
                    # Walk to anchor position
                    anchor_pos = AnchorPosition(sit_pos["entity_id"], sit_pos["anchor_ix"])
                    await self.highrise.walk_to(anchor_pos)
                else:
                    # Walk to normal position
                    await self.highrise.walk_to(Position(sit_pos['x'], sit_pos['y'], sit_pos['z'], sit_pos['facing']))
                
                await asyncio.sleep(1.0)
                # Ensure bot is sitting and not playing a dance emote
                # We can use "idle-loop-sitfloor" or similar if the goal is to sit on floor
                # If sitting on a chair, the walk_to(anchor_pos) usually handles the sitting state
                if sit_pos.get("type") != "anchor":
                    await self.highrise.send_emote("idle-loop-sitfloor")
                
                await self.highrise.chat("Done sitting successfully! 🪑")
            except Exception as e:
                await self.highrise.chat(f"❌ error: {str(e)}")
            return

        if msg == "!prizeon":
            if not await self.is_vip(user):
                return
            self.guess_face_game["prize_active"] = True
            await self.highrise.chat("✅ Prize system enabled! (Requires minimum players to start)")
            return

        if msg == "!prizeoff":
            if not await self.is_vip(user):
                return
            self.guess_face_game["prize_active"] = False
            await self.highrise.chat("❌ Prize system disabled!")
            return

        if msg.startswith("!prizeamount "):
            if not await self.is_vip(user):
                return
            try:
                amount = int(msg.split(" ")[1])
                # Validate that the amount is a valid tip tier
                valid_tiers = [1, 5, 10, 50, 100, 500, 1000]
                if amount not in valid_tiers:
                    await self.highrise.chat(f"❌ Invalid prize amount! Valid tiers are: {', '.join(map(str, valid_tiers))}")
                    return
                self.guess_face_game["prize_amount"] = amount
                await self.highrise.chat(f"💰 Prize amount set to: {amount} gold")
            except:
                await self.highrise.chat("❌ Please enter a valid number")
            return

        if msg.startswith("!reset"):
            if not await self.is_vip(user):
                return
            self.guess_face_game["min_players"] = 0
            self.guess_face_game["prize_active"] = False
            await self.highrise.chat("✅ Game settings reset! No minimum players, Prize system OFF.")
            return

        if msg.startswith("!prizeminimum "):
            if not await self.is_vip(user):
                return
            try:
                parts = msg.split(" ")
                if len(parts) < 2:
                    await self.highrise.chat("❌ Usage: !prizeminimum [number]")
                    return
                min_p = int(parts[1])
                self.guess_face_game["min_players"] = min_p
                await self.highrise.chat(f"👥 Minimum players required: {min_p}")
            except:
                await self.highrise.chat("❌ Please enter a valid number")
            return

        # Voting logic (now supports direct city name)
        allowed_cities = ["berlin", "reykjavik", "new york", "london", "moscow", "paris"]
        city_match = None
        for city in allowed_cities:
            if msg == city or msg.startswith(city + " "):
                city_match = city
                break
        
        if city_match:
            # Check if user is in players list and it's voting phase
            is_player = username in self.guess_face_game["players"]
            is_voting_phase = self.guess_face_game.get("phase") == "voting"
            
            if is_player and is_voting_phase:
                self.guess_face_game["votes"][username] = city_match
                await self.highrise.send_whisper(user.id, f"✅ Your vote for {city_match.upper()} has been recorded!")
                return
            elif is_player and not is_voting_phase:
                # Optional: inform player that it's not voting time yet
                pass
            # If not a player, we ignore the city name as a vote
        
        # Original !vote command (for backward compatibility or explicit use)
        if msg.startswith("!vote "):
            if username not in self.guess_face_game["players"]:
                return
            if self.guess_face_game.get("phase") != "voting":
                await self.highrise.send_whisper(user.id, "❌ Voting is not available right now!")
                return
                
            vote = msg[6:].strip().lower()
            if vote in allowed_cities:
                self.guess_face_game["votes"][username] = vote
                await self.highrise.send_whisper(user.id, f"✅ Your vote for {vote.upper()} has been recorded!")
            else:
                await self.highrise.chat(f"❌ Invalid city! Choose from: {', '.join(allowed_cities)}")
            return

        if msg.startswith("!join"):
            # Check if game is in a phase where joining is allowed
            if self.guess_face_game["active"] and self.guess_face_game.get("phase") not in ["waiting", None]:
                await self.highrise.chat(f"{username} ❌ Cannot join now - a round is in progress!")
                return
            
            # Custom countdown logic for prize/minimum players
            min_req = self.guess_face_game.get("min_players", 0)
            
            # Check if we should start countdown
            players_count = len(self.guess_face_game["players"])
            if not self.guess_face_game["active"] and not self.guess_face_game.get("starting"):
                if (min_req == 0 and players_count + 1 >= 1) or (min_req > 0 and players_count + 1 >= min_req):
                    await self.handle_join_command(user)
                    return
            
            await self.handle_join_command(user)
            return

        elif msg.startswith("!leave"):
            await self.handle_leave_command(user)

        elif msg.startswith("!hint"):
            await self.handle_hint_command(user)

        elif msg.startswith("!vote "):
            vote_word = msg[6:].strip().lower()
            await self.handle_vote_command(user, vote_word)
            return

        elif msg.startswith("!stats"):
            await self.handle_stats_command(user)

        elif msg.startswith("!rank"):
            await self.highrise.send_whisper(user.id, f"Balance: {self.credits[username]}g")

        elif msg.startswith("!ranklist"):
            await self.handle_ranklist_command(user)

        elif msg.startswith("eq "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            await self.equip_user(user, message)

        elif msg.startswith("freeze "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            target = msg.split(" ", 1)[1].replace("@", "").strip()
            if target:
                self.guess_face_game["frozen_players"].add(target)
                await self.highrise.chat(f"❄️ {target} is now FROZEN (stays on block, no danger zone)")
            return

        elif msg.startswith("kick "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            target = msg.split(" ", 1)[1].replace("@", "").strip()
            if target:
                # Remove from game players list
                if target in self.guess_face_game.get("players", []):
                    self.guess_face_game["players"].remove(target)
                
                # Add to excluded list so they can't rejoin this game
                self.guess_face_game["excluded_players"].add(target)
                
                # Teleport to exit if set
                exit_pos = self.guess_face_game.get("exit_pos")
                if exit_pos:
                    room_users_resp = await self.highrise.get_room_users()
                    room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                    for u, pos in room_users:
                        if u.username.lower() == target.lower():
                            t_pos = exit_pos
                            if "blocks" in exit_pos and exit_pos["blocks"]:
                                t_pos = exit_pos["blocks"][0]
                            await self.highrise.teleport(u.id, Position(t_pos['x'], t_pos['y'], t_pos['z'], t_pos['facing']))  # type: ignore
                            break
                
                await self.highrise.chat(f"👢 {target} has been kicked from the game!")
                
                # Check if game should end because only chooser or no players left
                available_players = [p for p in self.guess_face_game["players"] 
                                   if p not in self.guess_face_game.get("excluded_players", set())]
                
                # If only chooser left or no players left, end game
                if len(available_players) <= 1:
                    if len(available_players) == 0 or (len(available_players) == 1 and available_players[0] == self.guess_face_game.get("chosen_player")):
                        await self.highrise.chat("🏁 No players left in the game. Ending...")
                        # Reuse the logic from !end but safely
                        self.guess_face_game["active"] = False
                        self.guess_face_game["players"] = []
                        self.guess_face_game["phase"] = None
                        self.guess_face_game["game_ending"] = False
                        self.guess_face_game["chosen_player"] = None
                        self.guess_face_game["secret_word"] = None
                        self.guess_face_game["excluded_players"] = set()
            return

        elif msg.startswith("unfreeze "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            target = msg.split(" ", 1)[1].replace("@", "").strip()
            if target and target in self.guess_face_game["frozen_players"]:
                self.guess_face_game["frozen_players"].remove(target)
                await self.highrise.chat(f"🔥 {target} is now UNFROZEN (can go to danger zone)")
            return

        elif msg.startswith("!change chooser "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            # Extract the player name after "!change chooser "
            new_chooser = message[15:].replace("@", "").strip()
            
            # Find the player (case-insensitive search)
            found_player = None
            for player in self.guess_face_game["players"]:
                if player.lower() == new_chooser.lower():
                    found_player = player
                    break
            
            if not found_player:
                await self.highrise.chat(f"❌ Player '{new_chooser}' not found in game! Players: {', '.join(self.guess_face_game['players'])}")
                return
            
            new_chooser = found_player
            
            old_chooser = self.guess_face_game.get("chosen_player")
            self.guess_face_game["chosen_player"] = new_chooser
            self.guess_face_game["secret_word"] = None  # Reset secret word so they must choose again
            self.guess_face_game["votes"] = {}  # Clear any previous votes/selections
            
            # Return old chooser to their block if they exist
            if old_chooser and old_chooser in self.guess_face_game.get("player_positions", {}):
                old_chooser_idx = self.guess_face_game["player_positions"][old_chooser]
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                
                rows = self.guess_face_game.get("rows", {})
                blocks = self.guess_face_game.get("blocks", {})
                old_pos = None
                
                if rows and old_chooser_idx is not None:
                    current_count = 0
                    for row_key in sorted(rows.keys(), key=lambda x: int(x) if isinstance(x, str) else x):
                        row = rows[row_key]
                        num_blocks = row.get("num_blocks", len(row.get("blocks", [])))
                        if current_count + num_blocks > old_chooser_idx:
                            pos_in_row = old_chooser_idx - current_count
                            if "blocks" in row and pos_in_row < len(row["blocks"]):
                                block = row["blocks"][pos_in_row]
                                old_pos = Position(block['x'], block['y'], block['z'], block['facing'])
                            break
                        current_count += num_blocks
                
                if not old_pos and (str(old_chooser_idx) in blocks or old_chooser_idx in blocks):
                    b = blocks.get(old_chooser_idx) or blocks.get(str(old_chooser_idx))
                    if b:
                        old_pos = Position(b['x'], b['y'], b['z'], b['facing'])

                if old_pos:
                    for u, pos in room_users:
                        if u.username == old_chooser:
                            await self.highrise.teleport(u.id, old_pos)  # type: ignore
                            break
            
            # Teleport new chooser to chooser position
            chooser_pos = self.guess_face_game.get("chooser_pos")
            if chooser_pos:
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                # Get first block from blocks list (or use as direct position if old format)
                teleport_pos = chooser_pos
                if "blocks" in chooser_pos and chooser_pos["blocks"]:
                    teleport_pos = chooser_pos["blocks"][0]
                for u, pos in room_users:
                    if u.username == new_chooser:
                        await self.highrise.teleport(u.id, Position(teleport_pos['x'], teleport_pos['y'], teleport_pos['z'], teleport_pos['facing']))  # type: ignore
                        break
            
            await self.highrise.chat(f"🔄 Chooser changed: {old_chooser} → {new_chooser}")
            await self.highrise.chat(f"⚠️ Previous secret word cancelled. @{new_chooser} please whisper a new word!")
            return

        elif msg.startswith("put"):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            
            parts = msg.split(" ")
            mode = "manual" # Default to manual if just "put"
            if len(parts) > 1:
                if parts[1].lower() == "bot":
                    mode = "bot"
            
            current_chooser = self.guess_face_game.get("chosen_player")
            if not current_chooser:
                await self.highrise.chat("❌ No chooser is currently selected!")
                return
            
            # Check if chooser is in player positions (on a block)
            if current_chooser not in self.guess_face_game.get("player_positions", {}):
                await self.highrise.chat(f"❌ {current_chooser} is not assigned to any block!")
                return
            
            chooser_idx = self.guess_face_game["player_positions"][current_chooser]
            rows = self.guess_face_game.get("rows", {})
            blocks = self.guess_face_game.get("blocks", {})
            
            # Find the chooser's original block position
            original_pos = None
            if rows and chooser_idx is not None:
                current_count = 0
                for row_key in sorted(rows.keys(), key=lambda x: int(x) if isinstance(x, str) else x):
                    row = rows[row_key]
                    num_blocks = row.get("num_blocks", len(row.get("blocks", [])))
                    if current_count + num_blocks > chooser_idx:
                        pos_in_row = chooser_idx - current_count
                        if "blocks" in row and pos_in_row < len(row["blocks"]):
                            block = row["blocks"][pos_in_row]
                            original_pos = Position(block['x'], block['y'], block['z'], block['facing'])
                        break
                    current_count += num_blocks
            
            # Fallback to blocks dictionary
            if not original_pos and (str(chooser_idx) in blocks or chooser_idx in blocks):
                b = blocks.get(chooser_idx) or blocks.get(str(chooser_idx))
                if b:
                    original_pos = Position(b['x'], b['y'], b['z'], b['facing'])
            
            if original_pos:
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.username == current_chooser:
                        await self.highrise.teleport(u.id, original_pos)  # type: ignore
                        break
                
                if mode == "bot":
                    # Make bot the chooser
                    self.guess_face_game["chosen_player"] = "Bot"
                    self.guess_face_game["secret_word"] = random.choice(["berlin", "reykjavik", "new york", "london", "moscow", "paris"])
                    await self.highrise.chat(f"🔄 {current_chooser} returned. I will choose the word! 🤖")
                    await self.highrise.chat("✅ Word selected! 🤫 Start voting!")
                    asyncio.create_task(self.discussion_phase())
                else:
                    # Manual mode
                    self.guess_face_game["chosen_player"] = "Manual" 
                    self.guess_face_game["secret_word"] = None
                    await self.highrise.chat(f"🔄 {current_chooser} returned. Owner/Allowed users can whisper the city! 🤫")
            else:
                await self.highrise.chat(f"❌ Could not find {current_chooser}'s block position!")
            return

        elif msg.startswith("pull "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            
            target_name = msg.split(" ", 1)[1].replace("@", "").strip()
            if not target_name:
                await self.highrise.chat("Usage: pull @username")
                return
            
            # Find the player in the game
            found_player = None
            for player in self.guess_face_game.get("players", []):
                if player.lower() == target_name.lower():
                    found_player = player
                    break
            
            if not found_player:
                await self.highrise.chat(f"❌ Player '{target_name}' not found in game!")
                return
            
            # Get chooser position
            chooser_pos = self.guess_face_game.get("chooser_pos")
            if not chooser_pos:
                await self.highrise.chat("❌ Chooser position not set! Use 'set chooser' first.")
                return
            
            # Teleport player to chooser area
            try:
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                # Get first block from blocks list (or use as direct position if old format)
                teleport_pos = chooser_pos
                if "blocks" in chooser_pos and chooser_pos["blocks"]:
                    teleport_pos = chooser_pos["blocks"][0]
                for u, pos in room_users:
                    if u.username == found_player:
                        await self.highrise.teleport(u.id, Position(teleport_pos['x'], teleport_pos['y'], teleport_pos['z'], teleport_pos['facing']))  # type: ignore
                        await self.highrise.chat(f"🎯 {found_player} pulled to chooser area!")
                        break
            except Exception as e:
                await self.highrise.chat(f"❌ Error pulling {found_player}: {str(e)}")
            return

        elif msg.startswith("allow "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            target = msg.split(" ", 1)[1].replace("@", "").strip()
            if target:
                self.allowed_whispers.add(target)
                self.save_allowed_whispers()
                await self.highrise.chat(f"✅ {target} can now whisper to the bot!")
            return

        elif msg.startswith("disallow "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "This command is for the owner only.")
                return
            target = msg.split(" ", 1)[1].replace("@", "").strip()
            if target and target in self.allowed_whispers:
                self.allowed_whispers.remove(target)
                self.save_allowed_whispers()
                await self.highrise.chat(f"❌ {target} can no longer whisper to the bot!")
            return

        elif msg.startswith("!add_vip "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for the owner only.")
                return
            target = msg.split(" ", 1)[1].replace("@", "").strip()
            if target:
                self.vips.add(target)
                self.save_vips()
                await self.highrise.chat(f"⭐ {target} is now VIP (Moderator)!")
            return

        elif msg.startswith("!remove_vip "):
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for the owner only.")
                return
            target = msg.split(" ", 1)[1].replace("@", "").strip()
            if target and target in self.vips:
                self.vips.remove(target)
                self.save_vips()
                await self.highrise.chat(f"❌ {target} is no longer VIP!")
            return

        elif msg == "!vip_list":
            if not await self.is_owner(user):
                await self.highrise.send_whisper(user.id, "⛔ This command is for the owner only.")
                return
            if self.vips:
                vip_list = ", ".join(sorted(self.vips))
                await self.highrise.send_whisper(user.id, f"⭐ VIP Members ({len(self.vips)}):\n{vip_list}")
            else:
                await self.highrise.send_whisper(user.id, "❌ No VIP members yet!")
            return

        elif msg == "!vip":
            if not await self.is_vip(user):
                await self.highrise.send_whisper(user.id, "⛔ You must be VIP to use this command!")
                return
            
            vip_pos = self.guess_face_game.get("vip_pos")
            if not vip_pos:
                await self.highrise.send_whisper(user.id, "❌ VIP spot not set yet! Ask the owner to set it with 'set vip_spot'")
                return
            
            try:
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                # Get first block from blocks list (or use as direct position if old format)
                teleport_pos = vip_pos
                if "blocks" in vip_pos and vip_pos["blocks"]:
                    teleport_pos = vip_pos["blocks"][0]
                for u, pos in room_users:
                    if u.username == user.username:
                        await self.highrise.teleport(u.id, Position(teleport_pos['x'], teleport_pos['y'], teleport_pos['z'], teleport_pos['facing']))  # type: ignore
                        await self.highrise.chat(f"⭐ {user.username} joined the VIP area!")
                        break
            except Exception as e:
                await self.highrise.send_whisper(user.id, f"❌ Error teleporting: {str(e)}")
            return

        elif msg == "h":
            if not await self.is_vip(user):
                await self.highrise.send_whisper(user.id, "⛔ You must be VIP/Moderator to use this command!")
                return
            
            host_pos = self.guess_face_game.get("host_pos")
            if not host_pos:
                await self.highrise.send_whisper(user.id, "❌ Host spot not set yet! Ask the owner to set it with 'set host'")
                return
            
            try:
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                # Get first block from blocks list (or use as direct position if old format)
                teleport_pos = host_pos
                if "blocks" in host_pos and host_pos["blocks"]:
                    teleport_pos = host_pos["blocks"][0]
                for u, pos in room_users:
                    if u.username == user.username:
                        await self.highrise.teleport(u.id, Position(teleport_pos['x'], teleport_pos['y'], teleport_pos['z'], teleport_pos['facing']))  # type: ignore
                        await self.highrise.chat(f"👑 {user.username} is now hosting!")
                        break
            except Exception as e:
                await self.highrise.send_whisper(user.id, f"❌ Error teleporting: {str(e)}")
            return

    async def on_message(self, user_id: str, conversation_id: str, is_new_conversation: bool) -> None:  # type: ignore
        try:
            # Track that this user messaged the bot - add to list for !invite command
            self.users_messaged_bot.add(user_id)
            self.user_conversations[user_id] = conversation_id  # Store conversation ID for later invites
            self.save_users_messaged_bot()
            print(f"📝 Added {user_id} to users_messaged_bot (DM). Total: {len(self.users_messaged_bot)}")
            
            response = await self.highrise.get_messages(conversation_id)
            if not (response and hasattr(response, 'messages') and response.messages and len(response.messages) > 0):  # type: ignore
                return

            message = response.messages[0].content.strip()  # type: ignore
            msg = message.lower()

            # Create a dummy user object for compatibility with handle functions
            user = User(id=user_id, username="User") 

            if msg.startswith("!commands") or msg == "commands":
                # Clear conversation IDs are handled inside handle_commands_command
                await self.handle_commands_command(user, conversation_id)
            elif msg.startswith("!help") or msg == "help":
                await self.handle_help_command(user, conversation_id)
            elif msg.startswith("!stats") or msg == "stats":
                await self.handle_stats_command(user, conversation_id)
            elif msg.startswith("!invite") or msg == "invite":
                await self.send_invite_to_room(user, conversation_id)

        except Exception as e:
            print(f"Error in on_message: {e}")

    async def on_whisper(self, user: User, message: str) -> None:  # type: ignore
        username = user.username
        msg = message.lower().strip()

        print(f"\n📨📨📨 WHISPER RECEIVED FROM {username}: '{msg}'")

        # Track that this user messaged the bot
        self.users_messaged_bot.add(user.id)
        self.save_users_messaged_bot()
        
        if username not in self.balances:
            self.balances[username] = 1000
            self.save_balances()
        if username not in self.credits:
            self.credits[username] = 0
            self.save_credits()

        try:
            # Handle !commands/!invite in whisper
            if msg == "commands" or msg == "!commands":
                await self.handle_commands_command(user)
                return
            elif msg == "invite" or msg == "!invite":
                await self.send_invite_to_room(user)
                return

            # Allowed words for voting and choosing
            allowed_words = ["berlin", "reykjavik", "new york", "london", "moscow", "paris"]
            
            # Handle !vote command format
            if msg.startswith("!vote "):
                vote_word = msg[6:].strip().lower()  # Extract word after !vote
                # Create a user object with the vote word as message
                await self.handle_vote_command(user, vote_word)
                return
            
            # Check if user is the chooser and game is in "choosing" phase
            if username == self.guess_face_game.get("chosen_player") and self.guess_face_game.get("phase") == "choosing":
                # Chooser can ONLY whisper one of the 6 allowed words
                if msg in allowed_words:
                    # Cancel the timeout task since they whispered in time
                    if self.guess_face_game.get("chooser_timeout_task"):
                        self.guess_face_game["chooser_timeout_task"].cancel()
                        self.guess_face_game["chooser_timeout_task"] = None
                    
                    self.guess_face_game["secret_word"] = msg
                    await self.highrise.chat("✅ Word accepted! 🤫")
                    print(f"✅ Chooser {username} selected word: {msg}")
                    return
                else:
                    # Chooser tried to whisper an invalid word
                    await self.highrise.send_whisper(user.id, "❌ Invalid word! You can only choose from: BERLIN, REYKJAVIK, NEW YORK, LONDON, MOSCOW, PARIS")
                    return
            
            # During voting/discussion phase, accept votes from any player (allowed city names)
            if self.guess_face_game.get("phase") in ["voting", "discussion"]:
                if msg in allowed_words:
                    # Player is voting during discussion/voting phase
                    await self.handle_vote_command(user, msg)
                    return
                # If it's an authorized user, they can whisper other things
                elif username in self.allowed_whispers:
                    print(f"✅ Authorized whisper from {username}: {message}")
                    return
                # Otherwise silently ignore
                return
            
            # Check if user is authorized to whisper anything (for admin/owner commands)
            if username in self.allowed_whispers:
                # If they whisper an allowed city, handle it as a vote (if it's not chooser choosing phase)
                if msg in allowed_words:
                    await self.handle_vote_command(user, message)
                    return
                # Authorized player can whisper anything else (commands etc)
                print(f"✅ Authorized whisper from {username}: {message}")
                return
            
            # Ignore ALL other whispers silently (non-authorized players, non-voting words)
            return

        except Exception as e:
            import traceback
            print(f"❌ ERROR in on_whisper for {username}: {e}")
            print(traceback.format_exc())

    async def handle_join_command(self, user: User):
        username = user.username

        # Prevent joining while game is ending
        if self.guess_face_game.get("game_ending"):
            await self.highrise.chat(f"{username} ❌ Game is ending. Try again in a moment!")
            return

        # Check if player was already excluded (left the game)
        if username in self.guess_face_game.get("excluded_players", set()):
            await self.highrise.chat(f"{username} ❌ You cannot return - you already left this game")
            return

        # Only allow joining if game is NOT active, or if active but still in "waiting" phase
        if self.guess_face_game["active"] and self.guess_face_game.get("phase") != "waiting":
            await self.highrise.chat(f"{username} ❌ Cannot join now - game has already started!")
            return

        if self.guess_face_game["active"] and self.guess_face_game.get("phase") == "waiting":
            # Game is active but still in waiting phase, allow join
            await self.highrise.chat(f"{username} Joined the game! ⏳ Game starts in 1 minute! 🎮")
            if username not in self.guess_face_game["players"]:
                self.guess_face_game["players"].append(username)
                await self._teleport_player_to_position(user, username)
        else:
            # Game is not active yet, start it
            await self.highrise.chat(f"{username} Joined! ⏳ Game starts in 1 minute! 🎮")
            if username not in self.guess_face_game["players"]:
                self.guess_face_game["players"].append(username)
                await self._teleport_player_to_position(user, username)

            if not self.guess_face_game["active"]:
                self.guess_face_game["active"] = True
                self.guess_face_game["phase"] = "waiting"
                self.guess_face_game["excluded_players"] = set()
                asyncio.create_task(self.start_game_countdown())

    async def _teleport_player_to_position(self, user: User, username: str):
        """Helper to teleport player to correct row/block"""
        player_idx = len(self.guess_face_game["players"]) - 1
        
        # Try rows system first
        rows = self.guess_face_game.get("rows", {})
        if rows:
            # Find which row this player goes to
            current_player_count = 0
            target_row_idx = None
            pos_in_row = 0
            
            # Iterate through rows in order to find which row this player belongs to
            for row_idx in sorted(rows.keys(), key=lambda x: int(x) if isinstance(x, str) else x):
                row = rows[row_idx]
                num_blocks = row.get("num_blocks", len(row.get("blocks", [])))
                
                if current_player_count + num_blocks > player_idx:
                    # This player goes in this row
                    target_row_idx = row_idx
                    pos_in_row = player_idx - current_player_count
                    break
                
                current_player_count += num_blocks
            
            # Teleport player to their block
            if target_row_idx is not None and target_row_idx in rows:
                row = rows[target_row_idx]
                if "blocks" in row and pos_in_row < len(row["blocks"]):
                    block = row["blocks"][pos_in_row]
                    self.guess_face_game["player_positions"][username] = player_idx
                    await self.highrise.teleport(user.id, Position(block['x'], block['y'], block['z'], block['facing']))  # type: ignore
                    return
        
        # Fallback to blocks system
        blocks = self.guess_face_game.get("blocks", {})
        if str(player_idx) in blocks or player_idx in blocks:
            b = blocks.get(player_idx) or blocks.get(str(player_idx))
            self.guess_face_game["player_positions"][username] = player_idx
            await self.highrise.teleport(user.id, Position(b['x'], b['y'], b['z'], b['facing']))  # type: ignore

    async def start_game_countdown(self):
        # Start monitoring player positions during waiting phase
        asyncio.create_task(self.monitor_player_positions())
        
        # Countdown logic with minimum players requirement
        while True:
            min_req = self.guess_face_game.get("min_players", 0)
            
            # If there's a minimum requirement, wait until it's met
            if min_req > 0 and len(self.guess_face_game["players"]) < min_req:
                await self.highrise.chat(f"⏳ Waiting for players ({len(self.guess_face_game['players'])}/{min_req})")
                await asyncio.sleep(10)
                if not self.guess_face_game["active"]: return
                continue # Re-check min_req after waiting
            else:
                # Requirement met or no requirement
                break

        # One minute countdown (either after min players met, or immediately if no min)
        await self.highrise.chat("⏳ Game starts in 1 minute! 🎮")
        for i in range(50, 0, -10):
            await asyncio.sleep(10)
            if not self.guess_face_game["active"]: return
            
            # Re-fetch min_req in case it changed (e.g. !reset)
            min_req = self.guess_face_game.get("min_players", 0)
            
            # If min_req was set, ensure it's still met during the minute
            if min_req > 0 and len(self.guess_face_game["players"]) < min_req:
                await self.highrise.chat("❌ Countdown stopped - not enough players")
                self.guess_face_game["active"] = False
                return
            await self.highrise.chat(f"⏳ Game starts in {i} seconds...")

        # Final 10 seconds countdown
        for i in range(9, 0, -1):
            await self.highrise.chat(f"⏳ {i}")
            await asyncio.sleep(1)
            if not self.guess_face_game["active"]: return
            
            # Re-fetch min_req
            min_req = self.guess_face_game.get("min_players", 0)
            if min_req > 0 and len(self.guess_face_game["players"]) < min_req:
                await self.highrise.chat("❌ Countdown stopped - not enough players")
                self.guess_face_game["active"] = False
                return

        # Final check before starting
        min_req = self.guess_face_game.get("min_players", 0)
        # Allow any owner to play alone for testing
        is_owner_alone = len(self.guess_face_game["players"]) == 1 and any(owner in self.guess_face_game["players"] for owner in self.owner_usernames)

        if not self.guess_face_game["active"] or (min_req > 0 and len(self.guess_face_game["players"]) < min_req and not is_owner_alone):
            await self.highrise.chat("Not enough players. Game cancelled.")
            self.guess_face_game["active"] = False
            return

        # Now that game is actually starting, move players who left during waiting to excluded list
        self.guess_face_game["excluded_players"].update(self.guess_face_game.get("left_during_waiting", set()))
        self.guess_face_game["left_during_waiting"] = set()

        await self.start_new_round()

    async def monitor_player_positions(self):
        """Monitor players during waiting phase only - allow free movement during game"""
        while self.guess_face_game.get("active"):
            try:
                # Only enforce position restrictions during waiting phase
                if self.guess_face_game.get("phase") != "waiting":
                    # During game phases, players are free to move
                    # Bot only teleports them when needed (to danger zone, chooser area)
                    await asyncio.sleep(1)
                    continue
                
                # During waiting phase, enforce block positions
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                
                for player_name in self.guess_face_game.get("players", []):
                    # Skip excluded (eliminated) players
                    if player_name in self.guess_face_game.get("excluded_players", set()):
                        continue
                    
                    player_idx = self.guess_face_game.get("player_positions", {}).get(player_name)
                    rows = self.guess_face_game.get("rows", {})
                    blocks = self.guess_face_game.get("blocks", {})
                    
                    # Find expected position during waiting
                    expected_pos = None
                    
                    # Regular player - should stay on their block during waiting
                    if player_idx is not None:
                        if rows:
                            current_player_count = 0
                            target_row_idx = None
                            pos_in_row = 0
                            
                            for row_idx in sorted(rows.keys()):
                                row = rows[row_idx]
                                num_blocks = row.get("num_blocks", len(row.get("blocks", [])))
                                
                                if current_player_count + num_blocks > player_idx:
                                    target_row_idx = row_idx
                                    pos_in_row = player_idx - current_player_count
                                    break
                                
                                current_player_count += num_blocks
                            
                            if target_row_idx is not None and target_row_idx in rows:
                                row = rows[target_row_idx]
                                if "blocks" in row and pos_in_row < len(row["blocks"]):
                                    block = row["blocks"][pos_in_row]
                                    expected_pos = Position(block['x'], block['y'], block['z'], block['facing'])
                        
                        if not expected_pos and (str(player_idx) in blocks or player_idx in blocks):
                            b = blocks.get(player_idx) or blocks.get(str(player_idx))
                            if b:
                                expected_pos = Position(b['x'], b['y'], b['z'], b['facing'])
                    
                    # Teleport if moved during waiting phase
                    if expected_pos:
                        for u, pos in room_users:
                            if u.username == player_name:
                                # Check if player is too far from expected position
                                distance = ((pos.x - expected_pos.x)**2 + (pos.y - expected_pos.y)**2)**0.5  # type: ignore
                                if distance > 0.5:  # If moved more than 0.5 meters - allow small movements
                                    try:
                                        await self.highrise.teleport(u.id, expected_pos)  # type: ignore
                                    except:
                                        pass
                                break
                
                await asyncio.sleep(1)  # Check every 1 second during waiting
            except:
                await asyncio.sleep(1)

    async def start_new_round(self):
        self.guess_face_game["round"] += 1

        # Set phase to waiting to lock player movement
        self.guess_face_game["phase"] = "waiting"
        
        # Return all players to their blocks (except excluded ones and current chooser) before starting new round
        try:
            room_users_resp = await self.highrise.get_room_users()
            room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
            current_chooser = self.guess_face_game.get("chosen_player")
            
            for player_name in self.guess_face_game.get("players", []):
                # Skip excluded (eliminated) players and current chooser (he stays where he is)
                if player_name in self.guess_face_game.get("excluded_players", set()):
                    continue
                if player_name == current_chooser:
                    continue
                
                player_idx = self.guess_face_game.get("player_positions", {}).get(player_name)
                rows = self.guess_face_game.get("rows", {})
                blocks = self.guess_face_game.get("blocks", {})
                expected_pos = None
                
                # Find player's block position
                if player_idx is not None:
                    if rows:
                        current_count = 0
                        for row_key in sorted(rows.keys()):
                            row = rows[row_key]
                            num_blocks = row.get("num_blocks", len(row.get("blocks", [])))
                            
                            if current_count + num_blocks > player_idx:
                                pos_in_row = player_idx - current_count
                                if "blocks" in row and pos_in_row < len(row["blocks"]):
                                    block = row["blocks"][pos_in_row]
                                    expected_pos = Position(block['x'], block['y'], block['z'], block['facing'])
                                break
                            
                            current_count += num_blocks
                    
                    if not expected_pos and (str(player_idx) in blocks or player_idx in blocks):
                        b = blocks.get(player_idx) or blocks.get(str(player_idx))
                        if b:
                            expected_pos = Position(b['x'], b['y'], b['z'], b['facing'])
                
                # Teleport player to their block
                if expected_pos:
                    for u, pos in room_users:
                        if u.username == player_name:
                            await self.highrise.teleport(u.id, expected_pos)  # type: ignore
                            break
        except:
            pass

        # Wait 5 seconds with locked movement (waiting phase)
        await asyncio.sleep(5)

        # The chosen_player NEVER changes - same player for entire game
        chosen_username = self.guess_face_game.get("chosen_player")
        is_first_round = not chosen_username
        
        # If no chosen_player selected yet (first round), select one randomly
        if not chosen_username:
            available_players = [p for p in self.guess_face_game["players"] 
                               if p not in self.guess_face_game.get("excluded_players", set())]
            if not available_players:
                await self.highrise.chat("❌ No players available!")
                self.guess_face_game["active"] = False
                self.guess_face_game["players"] = []
                return
            # Ensure random selection from all available players across all rows
            chosen_username = random.choice(available_players)
            self.guess_face_game["chosen_player"] = chosen_username
            print(f"🎯 Randomly selected chooser: {chosen_username} from {len(available_players)} available players")
            
            # Explain the game flow on first round
            await self.highrise.chat("📋 Game Rules:\n\n1️⃣ Chooser whispers a secret word\n2️⃣ Other players discuss and guess\n3️⃣ Up to 5 players go to danger zone\n4️⃣ Game continues until 1 player remains 🏆")
        
        # Check if chosen_player has been eliminated
        if chosen_username in self.guess_face_game.get("excluded_players", set()):
            # Chooser was eliminated, select a new one randomly
            available_players = [p for p in self.guess_face_game["players"] 
                               if p not in self.guess_face_game.get("excluded_players", set())]
            if not available_players:
                await self.highrise.chat("❌ No available players! Game ended!")
                self.guess_face_game["active"] = False
                self.guess_face_game["players"] = []
                return
            
            # Pick a new chooser randomly
            chosen_username = random.choice(available_players)
            self.guess_face_game["chosen_player"] = chosen_username
            await self.highrise.chat(f"🔄 Previous chooser eliminated! New chooser selected: @{chosen_username}")
        
        self.guess_face_game["phase"] = "choosing"
        self.guess_face_game["secret_word"] = None
        self.guess_face_game["revealed_letters"] = set()
        self.guess_face_game["votes"] = {}
        self.guess_face_game["danger_zone_players"] = []
        self.guess_face_game["chooser_timeout_time"] = datetime.now()

        # Cancel any previous timeout task
        if self.guess_face_game.get("chooser_timeout_task"):
            self.guess_face_game["chooser_timeout_task"].cancel()
        
        # Start new timeout task (60 seconds)
        self.guess_face_game["chooser_timeout_task"] = asyncio.create_task(
            self._chooser_timeout_handler(chosen_username)
        )

        await self.highrise.chat(f"🎯 Round #{self.guess_face_game['round']}\n@{chosen_username} Choose a word! (⏰ 60 seconds)")

        # Only teleport chooser to area if it's the first round or if chooser changed
        # In subsequent rounds, chooser stays in their position (chooser_pos)
        if is_first_round or self.guess_face_game.get("previous_chooser") != chosen_username:
            # Save chooser's original position before teleporting
            chooser_original_pos = None
            room_users_resp = await self.highrise.get_room_users()
            room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
            for u, pos in room_users:
                if u.username == chosen_username:
                    chooser_original_pos = {"x": pos.x, "y": pos.y, "z": pos.z, "facing": pos.facing}  # type: ignore
                    break
            
            self.guess_face_game["chooser_original_pos"] = chooser_original_pos

        # Teleport chooser to area if set
        if "chooser_pos" in self.guess_face_game and self.guess_face_game["chooser_pos"]:
            c = self.guess_face_game["chooser_pos"]
            # Get first block from blocks list (or use as direct position if old format)
            teleport_pos = c
            if "blocks" in c and c["blocks"]:
                teleport_pos = c["blocks"][0]
            
            # Check if already there to avoid redundant teleport
            room_users_resp = await self.highrise.get_room_users()
            room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
            for u, pos in room_users:
                if u.username == chosen_username:
                    dx = pos.x - teleport_pos['x']  # type: ignore
                    dz = pos.z - teleport_pos['z']  # type: ignore
                    if (dx**2 + dz**2)**0.5 > 1.0:
                        await self.highrise.teleport(u.id, Position(teleport_pos['x'], teleport_pos['y'], teleport_pos['z'], teleport_pos['facing']))  # type: ignore
                    break
        
        # Remember the current chooser for next round
        self.guess_face_game["previous_chooser"] = chosen_username

        # Wait for player to choose (indefinite wait - no time limit)
        while not self.guess_face_game["secret_word"]:
            await asyncio.sleep(1)

        # Start discussion phase
        await self.discussion_phase()

    async def _chooser_timeout_handler(self, chosen_player: str):
        """Handle timeout if chooser doesn't whisper within 60 seconds"""
        try:
            # Wait 60 seconds
            await asyncio.sleep(60)
            
            # Check if we're still in choosing phase and same chooser
            if (self.guess_face_game.get("phase") == "choosing" and 
                self.guess_face_game.get("chosen_player") == chosen_player and
                not self.guess_face_game.get("secret_word")):
                
                print(f"⏰ TIMEOUT: Chooser {chosen_player} didn't whisper in time!")
                
                # Announce timeout
                await self.highrise.chat(f"⏰ {chosen_player} didn't choose in time! Eliminated and selecting new chooser...")
                
                # Eliminate this chooser
                self.guess_face_game["excluded_players"].add(chosen_player)
                self.guess_face_game["chosen_player"] = None
                
                # Check if only 1 player remains - they win!
                remaining_players = [p for p in self.guess_face_game.get("players", []) 
                                   if p not in self.guess_face_game.get("excluded_players", set())]
                
                if len(remaining_players) == 1:
                    # Game ends, winner declared
                    winner = remaining_players[0]
                    await self.highrise.chat("🏆🏆🏆 Game Over!")
                    await asyncio.sleep(1)
                    await self.highrise.chat(f"👑 @{winner} is the winner! 🎉")
                    
                    await asyncio.sleep(2)
                    
                    # Teleport winner to spawn position
                    spawn_pos = self.guess_face_game.get("spawn_pos")
                    if spawn_pos:
                        try:
                            room_users_resp = await self.highrise.get_room_users()
                            room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                            for u, pos in room_users:
                                if u.username == winner:
                                    await self.highrise.teleport(u.id, Position(  # type: ignore
                                        spawn_pos['x'], 
                                        spawn_pos['y'], 
                                        spawn_pos['z'], 
                                        spawn_pos['facing']
                                    ))
                                    await self.highrise.chat(f"✅ Done move winner @{winner} to Spawn!")
                                    break
                        except Exception as e:
                            print(f"Error teleporting winner: {e}")
                    
                    # Mark winner in stats
                    if winner in self.user_stats:
                        self.user_stats[winner]["games_won"] += 1
                        # Auto distribute prize for final winner
                        if self.guess_face_game.get("prize_active") and self.guess_face_game.get("prize_amount", 0) > 0:
                            amount = self.guess_face_game["prize_amount"]
                            try:
                                # Find winner user_id
                                winner_id = None
                                room_users_resp = await self.highrise.get_room_users()
                                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                                for u, pos in room_users:
                                    if u.username == winner:
                                        winner_id = u.id
                                        break
                                
                                if winner_id:
                                    # Tip amounts in Highrise are specific (1, 5, 10, 50, 100, 500, 1000, 5000, 10000)
                                    # We need to find the largest tip amount <= prize_amount
                                    possible_tips = [10000, 5000, 1000, 500, 100, 50, 10, 5, 1]
                                    remaining = amount
                                    for tip in possible_tips:
                                        while remaining >= tip:
                                            await self.highrise.tip_user(winner_id, f"gold_bar_{tip}")  # type: ignore
                                            remaining -= tip
                                    
                                    await self.highrise.chat(f"🎁 Congratulations @{winner}! Prize of {amount} gold sent! 💰🏆")
                                else:
                                    await self.highrise.chat(f"⚠️ Could not find winner @{winner} to send prize.")
                            except Exception as e:
                                print(f"Error tipping winner: {e}")
                                await self.highrise.chat(f"❌ Error sending gold to @{winner}. Check bot's gold balance.")
                    self.save_user_stats()
                    
                    # Reset game state
                    self.guess_face_game["active"] = False
                    self.guess_face_game["players"] = []
                    self.guess_face_game["chosen_player"] = None
                    self.guess_face_game["secret_word"] = None
                    self.guess_face_game["phase"] = None
                    self.guess_face_game["danger_zone_players"] = []
                    self.guess_face_game["excluded_players"] = set()
                    self.guess_face_game["game_ending"] = False
                    
                    # Ask to continue
                    await self.highrise.chat("🎮 New game? Type !join to start!")
                else:
                    # Start a new round with a new chooser
                    await asyncio.sleep(2)
                    await self.start_new_round()
        except asyncio.CancelledError:
            # Timeout was cancelled (player whispered in time)
            print(f"✅ Chooser {chosen_player} whispered in time, timeout cancelled")
            pass
        except Exception as e:
            print(f"❌ Error in chooser timeout handler: {e}")

    async def discussion_phase(self):
        secret = self.guess_face_game["secret_word"].lower()
        word_display = self.get_word_display(secret)
        
        # Set phase to discussion (allows voting during discussion)
        self.guess_face_game["phase"] = "discussion"
        self.guess_face_game["votes"] = {}  # Reset votes
        
        # Get eligible players (everyone except chooser and excluded)
        eligible_players = [p for p in self.guess_face_game["players"] 
                           if p != self.guess_face_game["chosen_player"] 
                           and p not in self.guess_face_game.get("excluded_players", set())]
        
        # await self.highrise.chat("💬 DISCUSSION & VOTING TIME!\n\nBERLIN 6\nREYKJAVIK 5\nNEW YORK 4\nLONDON 3\nMOSCOW 2\nPARIS 1")
        
        # Wait for all players to vote
        while True:
            # Only count players who are actually IN the game players list
            game_players = self.guess_face_game.get("players", [])
            votes_count = len([p for p in game_players if p in self.guess_face_game["votes"] and p != self.guess_face_game.get("chosen_player") and p not in self.guess_face_game.get("excluded_players", set())])
            
            # Eligible players are those in game, not chooser, and not eliminated
            eligible_players_in_game = [p for p in game_players 
                                     if p != self.guess_face_game.get("chosen_player") 
                                     and p not in self.guess_face_game.get("excluded_players", set())]
            
            # When all players in the game have voted
            if votes_count >= len(eligible_players_in_game) and len(eligible_players_in_game) > 0:
                await self.highrise.chat("✅ جميع players in the لعwithة صandتandا! اNoنتقthe  لDanger zone...")
                break
            
            # Safety check: players still in room AND in game
            room_users_resp = await self.highrise.get_room_users()
            room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
            active_in_game = [p for p in eligible_players_in_game if any(u.username == p for u, _ in room_users)]
            
            if votes_count >= len(active_in_game) and len(active_in_game) > 0:
                await self.highrise.chat("✅ جميع players the متandاجدين صandتandا! اNoنتقthe  لDanger zone...")
                break
            elif len(active_in_game) == 0:
                await self.highrise.chat("⚠️ No active game players left to vote. Moving on...")
                break
                
            await asyncio.sleep(2)

        # Immediately pull to danger zone
        await self.pull_to_danger_zone()

    async def pull_to_danger_zone(self):
        # Set phase to voting (for voting in danger zone)
        self.guess_face_game["phase"] = "voting"
        
        # Priority and limits for pulling players
        priority_config = {
            "berlin": 6,
            "reykjavik": 5,
            "new york": 4,
            "london": 3,
            "moscow": 2,
            "paris": 1
        }
        
        # The word that should be used for pulling is the secret word chosen by the chooser
        secret_word = self.guess_face_game.get("secret_word", "").lower().strip()
        if not secret_word:
            await self.highrise.chat("❌ No secret word set! Round ended.")
            await self.end_round()
            return

        # Get all current votes
        votes = self.guess_face_game.get("votes", {})
        eligible_players = [p for p in self.guess_face_game["players"] 
                           if p != self.guess_face_game["chosen_player"] 
                           and p not in self.guess_face_game.get("excluded_players", set())
                           and p not in self.guess_face_game.get("frozen_players", set())]
        
        # Only pull players who voted for the secret word
        voters_for_secret = [p for p in eligible_players if votes.get(p) == secret_word]
        
        # Limit based on the secret word (as specified by user)
        # BERLIN 6, REYKJAVIK 5, NEW YORK 4, LONDON 3, MOSCOW 2, PARIS 1
        priority_config = {
            "berlin": 6,
            "reykjavik": 5,
            "new york": 4,
            "london": 3,
            "moscow": 2,
            "paris": 1
        }
        
        # Apply the limit based on the secret word
        limit = priority_config.get(secret_word, 5) # Default to 5 if word not in config
        
        # Pull up to 'limit' players randomly from eligible players
        # The choice of the player doesn't matter, it's now random
        import random
        danger_players = random.sample(eligible_players, min(len(eligible_players), limit))
            
        self.guess_face_game["danger_zone_players"] = danger_players
        
        if danger_players:
            # await self.highrise.chat(f"🎲 TIME'S UP! The secret word was {secret_word.upper()}!")
            await self.highrise.chat(f"🎲 Pulling {len(danger_players)} players based on the city choice (Limit: {limit})! 🎲")
            await asyncio.sleep(1)
            
            player_names = ", ".join(danger_players)
            await self.highrise.chat(f"⚠️ Selected: {player_names}!")
            
            # Teleport all selected players to danger zone
            danger_pos = self.guess_face_game.get("danger_pos")
            player_positions = self.guess_face_game.get("player_positions", {})
            if danger_pos:
                try:
                    room_users_resp = await self.highrise.get_room_users()
                    room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                    
                    # Store original positions for all danger players
                    danger_original_positions = {}
                    
                    for danger_player in danger_players:
                        # Find and save original position
                        danger_original_pos = None
                        if danger_player in player_positions:
                            player_idx = player_positions[danger_player]
                            rows = self.guess_face_game.get("rows", {})
                            blocks = self.guess_face_game.get("blocks", {})
                            
                            # Try to find original position in rows
                            if rows:
                                current_count = 0
                                for row_key in sorted(rows.keys(), key=lambda x: int(x) if isinstance(x, str) else x):
                                    row = rows[row_key]
                                    num_blocks = row.get("num_blocks", len(row.get("blocks", [])))
                                    
                                    if current_count + num_blocks > player_idx:
                                        # Found the row
                                        pos_in_row = player_idx - current_count
                                        if "blocks" in row and pos_in_row < len(row["blocks"]):
                                            block = row["blocks"][pos_in_row]
                                            danger_original_pos = {
                                                "x": block['x'],
                                                "y": block['y'],
                                                "z": block['z'],
                                                "facing": block['facing']
                                            }
                                        break
                                    current_count += num_blocks
                            
                            # Fallback to blocks
                            if not danger_original_pos and (str(player_idx) in blocks or player_idx in blocks):
                                b = blocks.get(player_idx) or blocks.get(str(player_idx))
                                danger_original_pos = b
                        
                        danger_original_positions[danger_player] = danger_original_pos
                        
                        # Teleport player to danger zone
                        for u, pos in room_users:
                            if u.username == danger_player:
                                # Support both new rows format and old format
                                teleport_pos = None
                                
                                if "rows" in danger_pos:
                                    # New format: distribute players across rows
                                    p_danger_idx = danger_players.index(danger_player)
                                    rows_danger = danger_pos["rows"]
                                    row_keys = sorted(rows_danger.keys(), key=lambda x: int(x) if isinstance(x, str) else x)
                                    
                                    if row_keys:
                                        # Calculate which row and position in row
                                        current_pos = 0
                                        found_pos = False
                                        for row_key in row_keys:
                                            row_data = rows_danger[row_key]
                                            num_blocks = row_data.get("num_blocks", len(row_data.get("blocks", [])))
                                            
                                            if current_pos + num_blocks > p_danger_idx:
                                                # This player goes in this row
                                                pos_in_row = p_danger_idx - current_pos
                                                if "blocks" in row_data and pos_in_row < len(row_data["blocks"]):
                                                    teleport_pos = row_data["blocks"][pos_in_row]
                                                    found_pos = True
                                                break
                                            current_pos += num_blocks
                                        
                                        # If for some reason we didn't find a spot in the rows, use the first block of the first row
                                        if not found_pos and row_keys:
                                            first_row = rows_danger[row_keys[0]]
                                            if "blocks" in first_row and first_row["blocks"]:
                                                teleport_pos = first_row["blocks"][0]
                                
                                # Fallback to old format
                                if not teleport_pos:
                                    if "blocks" in danger_pos and danger_pos["blocks"]:
                                        teleport_pos = danger_pos["blocks"][0]
                                    else:
                                        teleport_pos = danger_pos
                                
                                await self.highrise.teleport(u.id, Position(  # type: ignore
                                    teleport_pos['x'], 
                                    teleport_pos['y'], 
                                    teleport_pos['z'], 
                                    teleport_pos['facing']
                                ))
                                await asyncio.sleep(0.3)
                                break
                    
                    # Store original positions for all danger players
                    self.guess_face_game["danger_original_positions"] = danger_original_positions
                    await self.highrise.chat(f"📍 All {len(danger_players)} players are in the danger zone!")
                    await asyncio.sleep(1)
                    
                    # Show voting options
                    # await self.highrise.chat("🗳️ Vote now!\n\nBERLIN 6\nREYKJAVIK 5\nNEW YORK 4\nLONDON 3\nMOSCOW 2\nPARIS 1")
                    
                except Exception as e:
                    print(f"Error teleporting players to danger zone: {e}")
        else:
            await self.highrise.chat(f"🎲 TIME'S UP! The secret word was {secret_word.upper()}!")
            await self.highrise.chat("ℹ️ No one guessed the secret word correctly.")
        
        await self.end_round()

    async def handle_hint_command(self, user: User):
        username = user.username

        # Check if user is excluded (eliminated from game)
        if username in self.guess_face_game.get("excluded_players", set()):
            await self.highrise.chat(f"{username} ❌ You have been eliminated from the game!")
            return

        if not self.guess_face_game["active"] or self.guess_face_game["phase"] != "discussion":
            await self.highrise.chat(f"{username} There is no active game right now")
            return

        if not self.guess_face_game["secret_word"]:
            return

        secret = self.guess_face_game["secret_word"].lower()
        unreveal_letters = [l for l in secret if l not in self.guess_face_game["revealed_letters"] and l.isalpha()]

        if not unreveal_letters:
            await self.highrise.chat(f"{username} All letters have been revealed!")
            return

        hint_letter = random.choice(unreveal_letters)
        self.guess_face_game["revealed_letters"].add(hint_letter)

        word_display = self.get_word_display(secret)
        await self.highrise.chat(f"💡 Hint: {word_display}")

    def get_word_display(self, word: str) -> str:
        display = ""
        for letter in word:
            if letter in self.guess_face_game["revealed_letters"]:
                display += letter + " "
            elif letter.isalpha():
                display += "_ "
            else:
                display += letter + " "
        return display.strip()

    async def handle_vote_command(self, user: User, message: str):
        username = user.username
        
        # Allowed words for voting
        allowed_words = ["berlin", "reykjavik", "new york", "london", "moscow", "paris"]

        # Check if user is excluded (eliminated from game)
        if username in self.guess_face_game.get("excluded_players", set()):
            await self.highrise.send_whisper(user.id, "❌ You have been eliminated from the game!")
            return

        # Check if user is the chooser (can't vote)
        if username == self.guess_face_game["chosen_player"]:
            await self.highrise.send_whisper(user.id, "You can't vote - you chose the word! 🚫")
            return

        # Check if user already voted
        if username in self.guess_face_game["votes"]:
            await self.highrise.send_whisper(user.id, "You already voted! 🗳️")
            return

        if not self.guess_face_game["active"] or (self.guess_face_game["phase"] != "discussion" and self.guess_face_game["phase"] != "voting"):
            await self.highrise.send_whisper(user.id, "There is no active game right now!")
            return

        try:
            vote = message.lower().strip()
            if not vote:
                return
            
            # Check if vote is in allowed words
            if vote not in allowed_words:
                await self.highrise.send_whisper(user.id, "Invalid vote! Allowed words: BERLIN, REYKJAVIK, NEW YORK, LONDON, MOSCOW, PARIS")
                return

            self.guess_face_game["votes"][username] = vote
            # Announce publicly that a player voted
            await self.highrise.chat(f"✅ {username} voted for {vote.upper()}")
        except Exception as e:
            await self.highrise.send_whisper(user.id, f"Error recording vote: {str(e)[:50]}")

    async def end_round(self):
        # Mark game as ending to prevent new joins during cleanup
        self.guess_face_game["game_ending"] = True
        
        secret = self.guess_face_game["secret_word"]

        if not secret:
            self.guess_face_game["active"] = False
            self.guess_face_game["players"] = []
            self.guess_face_game["excluded_players"] = set()
            self.guess_face_game["game_ending"] = False
            return

        # Count votes
        correct_votes = 0
        total_votes = 0
        correct_voters = []
        danger_players = self.guess_face_game.get("danger_zone_players", [])

        for voter, vote in self.guess_face_game["votes"].items():
            # Skip danger zone players from regular voting count (already handled)
            if voter in danger_players:
                continue
                
            total_votes += 1
            if vote == secret.lower():
                correct_votes += 1
                correct_voters.append(voter)
                # Update stats
                self.user_stats[voter]["games_won"] += 1
                self.user_stats[voter]["games_played"] += 1

        # Handle danger zone players results BEFORE announcing the answer
        if danger_players:
            # Wait longer to let everyone see the players in danger zone
            await asyncio.sleep(8)
            
            danger_original_positions = self.guess_face_game.get("danger_original_positions", {})
            room_users_resp = await self.highrise.get_room_users()
            room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
            exit_pos = self.guess_face_game.get("exit_pos")
            
            for danger_player in danger_players:
                if danger_player in self.guess_face_game["votes"]:
                    danger_vote = self.guess_face_game["votes"][danger_player]
                    
                    if danger_vote == secret.lower():
                        # Correct guess - return to original position
                        await self.highrise.chat(f"✅ {danger_player} CORRECT!")
                        # Add to correct voters list for the final announcement
                        correct_votes += 1
                        correct_voters.append(danger_player)
                        # Update stats for danger player
                        self.user_stats[danger_player]["games_won"] += 1
                        self.user_stats[danger_player]["games_played"] += 1
                        
                        # Teleport back to original position
                        danger_original_pos = danger_original_positions.get(danger_player)
                        if danger_original_pos:
                            for u, pos in room_users:
                                if u.username == danger_player:
                                    await self.highrise.teleport(u.id, Position(  # type: ignore
                                        danger_original_pos['x'], 
                                        danger_original_pos['y'], 
                                        danger_original_pos['z'], 
                                        danger_original_pos['facing']
                                    ))
                                    break
                    else:
                        # Wrong guess - move to exit
                        await self.highrise.chat(f"❌ {danger_player} WRONG! Moving to exit...")
                        # Update stats for danger player
                        self.user_stats[danger_player]["games_played"] += 1
                        self.user_stats[danger_player]["games_lost"] += 1
                        
                        # Add to excluded players
                        if "excluded_players" not in self.guess_face_game:
                            self.guess_face_game["excluded_players"] = set()
                        self.guess_face_game["excluded_players"].add(danger_player)
                        
                        # Teleport to exit if set
                        if exit_pos:
                            for u, pos in room_users:
                                if u.username == danger_player:
                                    await self.highrise.teleport(u.id, Position(  # type: ignore
                                        exit_pos['x'], 
                                        exit_pos['y'], 
                                        exit_pos['z'], 
                                        exit_pos['facing']
                                    ))
                                    break
                    
                    await asyncio.sleep(0.5)

        for player in self.guess_face_game["players"]:
            if player not in self.guess_face_game["votes"]:
                if player not in danger_players and player in self.user_stats:
                    self.user_stats[player]["games_played"] += 1
                    self.user_stats[player]["games_lost"] += 1

        self.save_user_stats()

        await asyncio.sleep(1)

        if correct_votes > 0:
            result = f"🎉 Correct! The word was: {secret}\n✅ Players who guessed correctly: {', '.join(correct_voters)}"
            await self.highrise.chat(result)
        await asyncio.sleep(3)

        # Check how many players are left (not excluded)
        remaining_players = [p for p in self.guess_face_game["players"] 
                           if p not in self.guess_face_game.get("excluded_players", set())]
        
        # Debug: Show remaining players count
        await self.highrise.chat(f"📊 Remaining players: {len(remaining_players)} ({', '.join(remaining_players) if remaining_players else 'none'})")
        
        # If only 1 player left, they are the WINNER!
        if len(remaining_players) == 1:
            winner = remaining_players[0]
            
            # Check if the last remaining player is the chooser
            if winner == self.guess_face_game.get("chosen_player"):
                await self.highrise.chat("🏆🏆🏆 Game Over!")
                await asyncio.sleep(1)
                await self.highrise.chat(f"👑 The Chooser {winner} has defeated everyone! 👑")
            else:
                await self.highrise.chat("🏆🏆🏆 Game Over!")
                await asyncio.sleep(1)
                await self.highrise.chat(f"👑 Final Winner: {winner}! 👑")
            
            await asyncio.sleep(2)
            
            # Teleport winner to spawn position
            spawn_pos = self.guess_face_game.get("spawn_pos")
            if spawn_pos:
                try:
                    room_users_resp = await self.highrise.get_room_users()
                    room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                    for u, pos in room_users:
                        if u.username == winner:
                            await self.highrise.teleport(u.id, Position(  # type: ignore
                                spawn_pos['x'], 
                                spawn_pos['y'], 
                                spawn_pos['z'], 
                                spawn_pos['facing']
                            ))
                            await self.highrise.chat(f"✅ {winner} teleported to entrance!")
                            break
                except Exception as e:
                    print(f"Error teleporting winner: {e}")
            
            # Mark winner in stats
            if winner in self.user_stats:
                self.user_stats[winner]["games_won"] += 1
                # Auto distribute prize for final winner
                if self.guess_face_game.get("prize_active") and self.guess_face_game.get("prize_amount", 0) > 0:
                    amount = self.guess_face_game["prize_amount"]
                    try:
                        # Find winner user_id
                        winner_id = None
                        room_users_resp = await self.highrise.get_room_users()
                        room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                        for u, pos in room_users:
                            if u.username == winner:
                                winner_id = u.id
                                break
                        
                        if winner_id:
                            # Try to tip the user
                            # Tip amounts in Highrise are specific (1, 5, 10, 50, 100, 500, 1000, 5000, 10000)
                            # We need to find the largest tip amount <= prize_amount
                            possible_tips = [10000, 5000, 1000, 500, 100, 50, 10, 5, 1]
                            remaining = amount
                            for tip in possible_tips:
                                while remaining >= tip:
                                    await self.highrise.tip_user(winner_id, f"gold_bar_{tip}")  # type: ignore
                                    remaining -= tip
                            
                            await self.highrise.chat(f"🎁 Congratulations {winner}! Sent {amount} real gold as prize! 💰🏆")
                        else:
                            await self.highrise.chat(f"⚠️ Could not find {winner} to send the prize.")
                    except Exception as e:
                        print(f"Error tipping winner: {e}")
                        await self.highrise.chat(f"❌ Error while trying to send gold to {winner}. Make sure the bot has enough balance.")
            self.save_user_stats()
            
            # Reset game state
            self.guess_face_game["active"] = False
            self.guess_face_game["players"] = []
            self.guess_face_game["chosen_player"] = None
            self.guess_face_game["secret_word"] = None
            self.guess_face_game["phase"] = None
            self.guess_face_game["danger_zone_players"] = []
            self.guess_face_game["excluded_players"] = set()
            self.guess_face_game["left_during_waiting"] = set()
            self.guess_face_game["game_ending"] = False
            
            # Ask to continue
            await self.highrise.chat("🎮 New game? Type !join to start!")
            return
        
        # If more than 1 player left, start next round automatically
        if len(remaining_players) > 1:
            # Check if chooser should win (if bot gets more correct guesses than players)
            # Or add a specific logic where chooser wins if they eliminate everyone
            await self.highrise.chat("⏸️ Next round starting...")
            await asyncio.sleep(2)
            
            # Reset for next round but keep players and excluded list
            # NOTE: Do NOT reset chosen_player here - start_new_round() needs it to return previous chooser to their block!
            self.guess_face_game["secret_word"] = None
            self.guess_face_game["revealed_letters"] = set()
            self.guess_face_game["votes"] = {}
            self.guess_face_game["phase"] = None
            self.guess_face_game["danger_zone_players"] = []
            
            # Start new round (will return previous chooser to original block, then select new chooser)
            await self.start_new_round()
            return
        
        # No players left at all (shouldn't happen but handle it)
        self.guess_face_game["active"] = False
        self.guess_face_game["players"] = []
        self.guess_face_game["excluded_players"] = set()
        self.guess_face_game["game_ending"] = False

    async def handle_leave_command(self, user: User):
        username = user.username

        if username not in self.guess_face_game["players"]:
            await self.highrise.send_whisper(user.id, "❌ You are not in the game!")
            return

        # Check if this player is the current chooser
        is_chooser = self.guess_face_game.get("chosen_player") == username
        phase = self.guess_face_game.get("phase")

        self.guess_face_game["players"].remove(username)
        
        # If game is in waiting phase (hasn't started), player can rejoin later
        if phase == "waiting":
            # Don't add to excluded_players - they can rejoin if they come back before game starts
            await self.highrise.chat(f"{username} left the game. You can rejoin before the game starts! 👋")
            await self.highrise.send_whisper(user.id, "✅ You left the game. Type !join to rejoin anytime before the game starts!")
        # If game has started (beyond waiting phase), exclude player from rejoining
        elif phase:
            self.guess_face_game["excluded_players"].add(username)
            await self.highrise.chat(f"{username} left the game. Cannot rejoin once game has started! 👋")
            await self.highrise.send_whisper(user.id, "✅ You left the game.")
            
            # CRITICAL: If chooser leaves during choosing phase, we need to pick a new one
            if is_chooser and phase == "choosing":
                print(f"⚠️ Chooser {username} left during choosing phase! Selecting replacement...")
                await self.highrise.chat(f"⚠️ Chooser @{username} has left! Selecting a replacement...")
                
                # Cancel timeout task
                if self.guess_face_game.get("chooser_timeout_task"):
                    self.guess_face_game["chooser_timeout_task"].cancel()
                
                # Reset word and chooser
                self.guess_face_game["secret_word"] = None
                self.guess_face_game["chosen_player"] = None
                
                # Trigger a new chooser selection by calling start_new_round logic part
                # Wait a bit before starting new round logic
                asyncio.create_task(self._handle_chooser_replacement())
        else:
            await self.highrise.chat(f"{username} left the game 👋")
            await self.highrise.send_whisper(user.id, "✅ You left the game.")
        
        # Teleport back to spawn/entrance if set
        spawn_pos = self.guess_face_game.get("spawn_pos")
        if spawn_pos:
            try:
                await self.highrise.teleport(user.id, Position(spawn_pos['x'], spawn_pos['y'], spawn_pos['z'], spawn_pos['facing']))  # type: ignore
            except:
                pass  # Ignore if teleport fails
        
        # Clear player position tracking
        if username in self.guess_face_game.get("player_positions", {}):
            del self.guess_face_game["player_positions"][username]

        # Check if only 1 player remains - they win!
        if len(self.guess_face_game["players"]) == 1:
            winner = self.guess_face_game["players"][0]
            await self.highrise.chat(f"🏆 {winner} is the winner! 🎉")
            
            # Update winner stats
            if winner in self.user_stats:
                self.user_stats[winner]["games_won"] += 1
                self.user_stats[winner]["games_played"] += 1
            self.save_user_stats()
            
            # Reset game
            self.guess_face_game["active"] = False
            self.guess_face_game["players"] = []
            self.guess_face_game["excluded_players"] = set()
            self.guess_face_game["phase"] = None
            
            # Teleport winner to exit if set
            try:
                room_users = (await self.highrise.get_room_users()).content  # type: ignore
                for u, pos in room_users:
                    if u.username == winner:
                        exit_pos = self.guess_face_game.get("exit_pos")
                        if exit_pos:
                            await self.highrise.teleport(u.id, Position(exit_pos['x'], exit_pos['y'], exit_pos['z'], exit_pos['facing']))  # type: ignore
                        break
            except:
                pass
        elif len(self.guess_face_game["players"]) == 0:
            self.guess_face_game["active"] = False
            self.guess_face_game["phase"] = None

    async def _handle_chooser_replacement(self):
        """Helper to replace chooser if they leave during choosing phase"""
        await asyncio.sleep(2)
        if self.guess_face_game["active"]:
            await self.start_new_round()

    async def handle_stats_command(self, user: User, conversation_id: str = None):  # type: ignore
        username = user.username
        stats = self.user_stats.get(username, {
            "games_played": 0,
            "games_won": 0,
            "games_lost": 0,
            "total_wagered": 0,
            "total_won": 0,
            "biggest_win": 0,
            "join_date": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        })

        win_rate = (stats["games_won"] / stats["games_played"] * 100) if stats["games_played"] > 0 else 0

        msg = """📊 Your Stats:
Games played: {stats['games_played']}
Wins: {stats['games_won']}
Losses: {stats['games_lost']}
Win rate: {win_rate:.1f}%"""

        if conversation_id:
            await self.highrise.send_message(conversation_id, msg)
        else:
            await self.highrise.send_whisper(user.id, msg)

    async def handle_ranklist_command(self, user: User):
        # Get top 5 players by wins
        sorted_players = sorted(
            self.user_stats.items(),
            key=lambda x: x[1].get("games_won", 0),
            reverse=True
        )[:5]

        msg = "🏆 Top 5 Players:\n"
        for i, (username, stats) in enumerate(sorted_players, 1):
            msg += f"{i}. {username}: {stats.get('games_won', 0)} wins\n"

        await self.highrise.send_whisper(user.id, msg)

    async def handle_commands_command(self, user: User, conversation_id: str = None):  # type: ignore
        """Send the list of commands to the user in a formatted message via DM or Whisper"""
        commands_text = (
            "🏆 **Prize System (Owner Only):**\n"
            "• !prizeon - Enable prizes & min players\n"
            "• !prizeoff - Disable prize system\n"
            "• !prizeamount [num] - Set gold prize (1,5,10,50,100,500,1000)\n"
            "• !prizeminimum [num] - Set min players to start\n"
            "• !reset - Reset min players & disable prize system\n\n"
            "📋 **General Commands (Players):**\n"
            "• !join - Join the game (teleports to block)\n"
            "• !leave - Leave the game\n"
            "• !vote [city] - Vote for a city\n"
            "• [City Name] - Quick vote by typing city name\n"
            "• !stats - View your wins/losses\n"
            "• !hint - Get a letter hint\n\n"
            "👑 **Moderation (Owner/Mod):**\n"
            "• config - (DM) View room status\n"
            "• !end - End current game round\n"
            "• kick @user - Remove player from round\n"
            "• !change chooser @user - Change current chooser\n"
            "• freeze/unfreeze @user - Toggle danger zone movement\n"
            "• sit - Make bot sit in its chair\n"
            "• put - Return chooser & allow manual word\n"
            "• put bot - Return chooser & auto-pick word\n"
            "• pull @user - Pull player to chooser area\n\n"
            "⚙️ **Setup (Owner Only):**\n"
            "• set row [num] [blocks] - Configure player row\n"
            "• rows - List saved rows\n"
            "• set chooser - Set chooser area\n"
            "• set danger - Set danger zone\n"
            "• set danger1/danger2 [blocks]\n"
            "• set spawn/exit/chair\n"
            "• save down / down - Mod teleport points"
        )
        
        try:
            # Prefer sending via DM (conversation_id or stored)
            target_conv = conversation_id or self.user_conversations.get(user.id)
            
            if target_conv:
                # Send the commands list
                chunks = split_message(commands_text, 200)
                for chunk in chunks:
                    try:
                        await self.highrise.send_message(target_conv, chunk)
                    except:
                        pass
                    await asyncio.sleep(0.3)
                
                # Inform about the DM if triggered from public chat
                if not conversation_id: 
                    await self.highrise.send_whisper(user.id, "📩 I've sent the commands to your DMs!")
                print(f"✅ Commands sent to @{user.username} via DM")
            else:
                # Fallback to whisper if no DM channel exists
                chunks = split_message(commands_text, 200)
                for chunk in chunks:
                    await self.highrise.send_whisper(user.id, chunk)
                    await asyncio.sleep(0.3)
                await self.highrise.send_whisper(user.id, "💡 Note: DM me first for a cleaner commands list!")
        except Exception as e:
            print(f"Error sending commands: {e}")
            await self.highrise.send_whisper(user.id, "⚠️ Error sending commands. Please try again.")

    async def handle_help_command(self, user: User, conversation_id: str = None):  # type: ignore
        help_msg = """🎮 GUESS FACE GAME - Complete Rules

📋 HOW THE GAME WORKS:

🎯 PHASE 1 - JOINING (Waiting Phase)
1. Send !join to join the game
2. Bot teleports you to your assigned block
3. Wait until game starts (1 minute countdown)
4. All players must stay on their blocks during this phase

🎯 PHASE 2 - CHOOSING (30 seconds)
1. Bot randomly picks one player as the CHOOSER
2. Chooser teleports to the Chooser area
3. Chooser whispers a secret word to the bot (from the 6 cities)
4. Other players discuss and guess the word

🎯 PHASE 3 - DISCUSSION (45 seconds)
1. Bot randomly pulls players to the Danger Zone
2. Players in danger zone must vote to eliminate someone
3. Other players discuss and guess the word
4. Use !hint to reveal one letter of the word

🎯 PHASE 4 - VOTING (30 seconds)
1. All players vote: !vote word or whisper the city name
2. Danger zone players MUST vote
3. Correct guess → go back to your block
4. Wrong guess → eliminated (go to exit area)

🎯 PHASE 5 - NEW ROUND (5 second break)
- If 2+ players remain → New round starts
- Bot picks a new Chooser
- Process repeats...

👑 WINNER: Last player remaining wins the round!

⚙️ IMPORTANT NOTES:
• Chooser CANNOT vote
• Each player votes only ONCE per phase
• You must stay on your block during waiting
• Eliminated players cannot rejoin
• Game continues until 1 player remains

📍 VALID CITIES (for voting):
BERLIN, REYKJAVIK, NEW YORK, LONDON, MOSCOW, PARIS

Good luck! 🍀"""

        chunks = split_message(help_msg, 200)
        for chunk in chunks:
            if conversation_id:
                await self.highrise.send_message(conversation_id, chunk)
            else:
                await self.highrise.send_whisper(user.id, chunk)
            await asyncio.sleep(0.3)

    async def equip_user(self, user: User, message: str):
        try:
            parts = message.split(" ")
            if len(parts) < 2:
                await self.highrise.send_whisper(user.id, "Usage: eq @username")
                return

            target_username = parts[1].replace("@", "")
            target_id = None
            target_outfit = None

            try:
                room_users_resp = await self.highrise.get_room_users()
                room_users = room_users_resp.content if hasattr(room_users_resp, 'content') else []  # type: ignore
                for u, pos in room_users:
                    if u.username.lower() == target_username.lower():
                        target_id = u.id
                        break
            except Exception as e:
                print(f"Error searching room: {e}")

            if target_id is None:
                await self.highrise.chat(f"Searching for @{target_username}...")
                target_id = await self.get_user_id_by_username(target_username)

                if target_id is None:
                    await self.highrise.chat(f"User @{target_username} not found")
                    return

            try:
                api_url = f"https://webapi.highrise.game/users/{target_id}"
                response = requests.get(api_url, timeout=10)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("user") and data["user"].get("outfit"):
                        api_outfit = data["user"]["outfit"]
                        print(f"Found {len(api_outfit)} outfit items")
                        target_outfit = []
                        for item in api_outfit:
                            item_id = item.get("item_id", item.get("id", ""))
                            if item_id:
                                target_outfit.append(Item(
                                    type='clothing',
                                    amount=1,
                                    id=item_id,
                                    account_bound=False,
                                    active_palette=item.get("active_palette", -1)
                                ))
                        print(f"Converted {len(target_outfit)} items for outfit")
                else:
                    print(f"Web API error: {response.status_code}")
            except Exception as e:
                print(f"Web API error: {e}")

            if not target_outfit:
                try:
                    outfit_response = await self.highrise.get_user_outfit(target_id)
                    target_outfit = outfit_response.outfit if hasattr(outfit_response, 'outfit') else None  # type: ignore
                except Exception as e:
                    print(f"get_user_outfit error: {e}")
                    await self.highrise.chat("Could not get user's outfit")
                    return

            if not target_outfit:
                await self.highrise.chat("No outfit found for this user")
                return

            try:
                print(f"Applying outfit with {len(target_outfit)} items...")
                await self.highrise.set_outfit(target_outfit)
                print("Outfit applied successfully!")
                await self.highrise.chat(f"Copied @{target_username}'s outfit!")
            except Exception as outfit_error:
                error_msg = str(outfit_error)
                print(f"Outfit apply error: {error_msg}")
                if "not owned" in error_msg.lower() or "item" in error_msg.lower():
                    await self.highrise.chat(f"Bot doesn't own some items from @{target_username}'s outfit")
                else:
                    await self.highrise.chat(f"Error applying outfit: {error_msg[:100]}")

        except Exception as e:
            print(f"Equip error: {e}")
            await self.highrise.chat("Error executing command")

    async def get_user_id_by_username(self, username: str) -> str | None:  # type: ignore
        try:
            api_url = f"https://create.highrise.game/api/users?username={username}"
            response = requests.get(api_url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                users_list = data.get("users", [])
                if users_list and len(users_list) > 0:
                    user_data = users_list[0]
                    user_id = user_data.get("user_id")
                    real_username = user_data.get("username", username)
                    if user_id:
                        print(f"API: Found {real_username} -> {user_id}")
                        return user_id
                else:
                    print(f"API: User '{username}' not found")
            else:
                print(f"API error: {response.status_code}")
        except Exception as e:
            print(f"API request failed: {e}")

        return None

    async def send_invite_to_room(self, user: User, conversation_id: str | None = None) -> None:  # type: ignore
        """Send a room invite to the user via WebAPI using the native 'invite' message type"""
        try:
            if not user.id or not isinstance(user.id, str):
                print(f"❌ Invalid user ID for {user.username}: {user.id}")
                return

            room_id = self.room_id or os.getenv("HIGHRISE_ROOM_ID", "665339cebb0667c76e14c27d")
            
            # Select conversation ID
            conv_id = conversation_id or self.user_conversations.get(user.id)
            
            if conv_id:
                try:
                    # Highrise SDK uses positional arguments: (conversation_id, content, type, room_id)
                    await self.highrise.send_message(
                        conv_id,
                        "دخول الغرفة 🎮",
                        "invite",
                        room_id
                    )
                    print(f"✅ Native invite sent to @{user.username}!")
                except Exception as e:
                    print(f"⚠️ Native invite failed, falling back to link: {e}")
                    invite_link = f"https://webapi.highrise.game/rooms/{room_id}"
                    try:
                        await self.highrise.send_message(conv_id, f"💌 Join my room! 🎮\n{invite_link}")
                    except:
                        pass
            else:
                print(f"❌ Could not send invite to @{user.username} - no conversation found")
        except Exception as e:
            print(f"❌ Error in send_invite_to_room: {e}")

    async def is_owner(self, user: User) -> bool:
        if not user or not user.username:
            return False
        return user.username.lower() in [name.lower() for name in self.owner_usernames if name]

    async def save_data_periodically(self):
        """Protected periodic save loop with error recovery"""
        while True:
            try:
                await asyncio.sleep(300)
                self.save_all_data()
            except Exception as e:
                print(f"Save data error: {e}")
                try:
                    self.save_all_data()
                except:
                    pass
                await asyncio.sleep(10)

    def save_all_data(self):
        self.save_credits()
        self.save_balances()
        self.save_user_stats()
        self.save_daily_rewards()
        self.save_game_config()
        self.save_allowed_whispers()
        self.save_vips()
        self.save_users_messaged_bot()
        self.save_user_conversations()
        self.save_room_id()
        self.save_invited_users()

    def save_balances(self):
        with open("balances.json", "w") as f:
            json.dump(self.balances, f)

    def load_balances(self):
        try:
            with open("balances.json", "r") as f:
                data = f.read().strip()
                if not data:
                    return {}
                return json.loads(data)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_credits(self):
        with open("credits.json", "w") as f:
            json.dump(self.credits, f)

    def load_credits(self):
        try:
            with open("credits.json", "r") as f:
                data = f.read().strip()
                if not data:
                    return {}
                return json.loads(data)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_user_stats(self):
        with open("user_stats.json", "w") as f:
            json.dump(self.user_stats, f)

    def load_user_stats(self):
        try:
            with open("user_stats.json", "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_daily_rewards(self):
        with open("daily_rewards.json", "w") as f:
            json.dump(self.daily_rewards, f)

    def load_daily_rewards(self):
        try:
            with open("daily_rewards.json", "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_allowed_whispers(self):
        with open("allowed_whispers.json", "w") as f:
            json.dump(list(self.allowed_whispers), f)

    def load_allowed_whispers(self):
        try:
            with open("allowed_whispers.json", "r") as f:
                data = json.load(f)
                return set(data) if data else set()
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def save_vips(self):
        with open("vips.json", "w") as f:
            json.dump(list(self.vips), f)

    def load_vips(self):
        try:
            with open("vips.json", "r") as f:
                data = json.load(f)
                return set(data) if data else set()
        except (FileNotFoundError, json.JSONDecodeError):
            return set()
    
    def save_users_messaged_bot(self):
        with open("users_messaged_bot.json", "w") as f:
            json.dump(list(self.users_messaged_bot), f)
    
    def load_users_messaged_bot(self):
        try:
            with open("users_messaged_bot.json", "r") as f:
                data = json.load(f)
                return set(data) if data else set()
        except (FileNotFoundError, json.JSONDecodeError):
            return set()
    
    def save_user_conversations(self):
        with open("user_conversations.json", "w") as f:
            json.dump(self.user_conversations, f)
    
    def load_user_conversations(self):
        try:
            with open("user_conversations.json", "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def save_room_id(self):
        with open("room_id.json", "w") as f:
            json.dump({"room_id": self.current_room_id}, f)
    
    def load_room_id(self):
        try:
            with open("room_id.json", "r") as f:
                data = json.load(f)
                return data.get("room_id", os.getenv("HIGHRISE_ROOM_ID", "665339cebb0667c76e14c27d"))
        except (FileNotFoundError, json.JSONDecodeError):
            return os.getenv("HIGHRISE_ROOM_ID", "665339cebb0667c76e14c27d")
    
    def save_invited_users(self):
        with open("invited_users.json", "w") as f:
            json.dump(list(self.invited_users), f)
    
    def load_invited_users(self):
        try:
            with open("invited_users.json", "r") as f:
                data = json.load(f)
                return set(data) if data else set()
        except (FileNotFoundError, json.JSONDecodeError):
            return set()
    
    async def is_vip(self, user: User) -> bool:
        # Check if the user is a room moderator
        # get_room_privileges is no longer available in the current highrise-python SDK
        # Fallback to manual owner/VIP list
        try:
            if not user or not user.username:
                return False
            return user.username in self.vips or await self.is_owner(user)
        except Exception as e:
            print(f"Error in is_vip for {getattr(user, 'username', 'unknown')}: {e}")
            return False


class WebServer():
    def __init__(self):
        self.app = Flask(__name__)
        @self.app.route('/')
        def index() -> str:
            return "Guess Face Game Bot is running!"
    def run(self) -> None:
        self.app.run(host='0.0.0.0', port=3000)
    def keep_alive(self):
        t = Thread(target=self.run)
        t.start()


class RunBot():
    room_id = os.getenv("HIGHRISE_ROOM_ID", "672f16c63fe53a88e79e6f23")
    bot_token = os.getenv("HIGHRISE_BOT_TOKEN", "e9f10ca5302ab0dfd857f02d363496f3a185c3612ff9a3fc58f6cce0c762ecb0")
    bot_file = "main"
    bot_class = "Mybot"

    def __init__(self) -> None:
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 999999  # Infinite retries
        self.reconnect_delay = 5
        self.definitions = [
            BotDefinition(  # type: ignore
                getattr(import_module(self.bot_file), self.bot_class)(),
                self.room_id, self.bot_token)
        ]

    def run_loop(self) -> None:
        """Main bot loop with auto-recovery and reconnection handling"""
        while True:
            try:
                self.reconnect_attempts = 0
                print("[BOT] Starting bot connection...")
                # Use a fresh bot instance on each connection attempt to reset state
                bot_instance = getattr(import_module(self.bot_file), self.bot_class)()
                definitions = [BotDefinition(bot_instance, self.room_id, self.bot_token)]  # type: ignore
                arun(main(definitions))  # type: ignore
            except Exception as e:
                self.reconnect_attempts += 1
                import traceback
                print(f"[BOT] Connection lost (attempt {self.reconnect_attempts}):")
                print(f"[BOT] Error: {e}")
                traceback.print_exc()

                # Reset counter to avoid overflow after 1000 attempts
                if self.reconnect_attempts > 1000:
                    self.reconnect_attempts = 1

                # Progressive backoff: start at 5s, max 30s
                delay = min(self.reconnect_delay * (2 ** min(self.reconnect_attempts - 1, 3)), 30)
                print(f"[BOT] Reconnecting in {delay}s...")
                time.sleep(delay)
            except KeyboardInterrupt:
                print("[BOT] Bot stopped by user")
                break


if __name__ == "__main__":
    WebServer().keep_alive()
    RunBot().run_loop()