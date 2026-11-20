"""
Request handler for multiple customers.

GET /customers
GET /customers/{customer_email}
POST /customers
"""
import json
import math
import pymongo
import validators

from pymongo import MongoClient, errors
from .base_handler import BaseHandler

DB = BaseHandler.CLIENT["Rewards"]

def _validate_customers_request(data):
    """
    Validate request payloads for the create customer endpoint.

    Parameters:
        data (dict): The request body in a single dictionary

    Returns:
        dict: Error code dict to use for response or validated data to build customer document with.
    """
    validation_errors = {}

    # Validate email, update message + errors array if invalid
    if not validators.email(data["email"]):
        validation_errors["message"] = "Invalid email."
        validation_errors["errors"] = {"email": ["The given email is invalid."]}

    # Format the order total to make sure it's a valid monetary value. Only 2 decimal places.
    formatted_order_total = f"{data['orderTotal']:.2f}"
    
    # Validate orderTotal, update message + errors array if invalid
    # Using >= because then we know there's more decimals. This will allow for .00 or no decimal to be passed
    if len(str(data["orderTotal"])) >= len(formatted_order_total):
        if "message" in validation_errors.keys():
            validation_errors["message"] += " and 1 more."
        else:
            validation_errors["message"] = "Invalid monetary value."

        error_row = {"orders": ["The monetary value is invalid. Round to nearest cent."]}
        validation_errors["errors"] = (
            validation_errors["errors"] | error_row
            if "errors" in validation_errors.keys()
            else error_row
        )

    
    return _build_customer_rewards(data) if len(validation_errors) == 0 else validation_errors

def _build_customer_rewards(validated_data):
    """
    Gets reward data for a customer based on their order totals and existing points.

    Parameters:
        validated_data: The order data to build a Customer with, validated in _validate_customers_request

    Returns:
        dict: A dictionary with the customer's email and reward-based data.
    """
    # Create customer with data provided if the data is valid
    email = validated_data["email"]
    existing_customer = DB.customers.find_one({"email": email})

    # Add total to points on existing customer's account if they exist, otherwise just use the order total
    total_points = math.floor(
        validated_data["orderTotal"] + existing_customer["rewardPoints"]
        if existing_customer
        else validated_data["orderTotal"]
    )

    # Nearest reward point total will always be an integer, rounded down to the nearest hundred
    reward_points = int((total_points // 100) * 100)

    # If the reward points are under the first tier the user should have None.
    if reward_points < 100:
        current_reward = None
        next_reward = DB.rewards.find_one({"points": {"$gte": reward_points}})
    else:
        # Find the relevant reward and the next reward
        rewards = list(
            DB.rewards.find(
                {"points": {"$gte": reward_points}}
            )
            .sort("points", pymongo.ASCENDING)
            .limit(2)
        )

        current_reward, next_reward = rewards[0], rewards[1]

    # Next tier progress
    next_tier_progress = round(1 - ((next_reward["points"] - total_points) / 100), 2)

    return {
        "email": email,
        "rewardPoints": total_points,
        "rewardTier": current_reward["tier"] if current_reward else None,
        "rewardTierName": current_reward["rewardName"] if current_reward else None,
        "nextRewardTier": next_reward["tier"],
        "nextRewardTierProgress": next_tier_progress,
    }

class CustomersHandler(BaseHandler):

    async def get(self, email):
        """
        Get all the customers

        Supports endpoints: GET /customers, GET /customers/{customer_email}
        TODO: Add filtering, pagination, Support for single customer
        """
        # Send json of customers if they're in the db, otherwise empty 200 response
        try:
            if not email:
                customers = list(DB.customers.find({}, {"_id": 0}))
                self.write(json.dumps(customers, indent=4))
            else:
                # Implement email logic here, for now return first where email is email
                self.write({})
        except (errors.CollectionInvalid, TypeError):
            # Return an empty json response if nothing's been created yet
            self.write({})

    async def post(self, _):
        """
        Create or update a customer with customer rewards.
        
        If an email already exists in the db, update the customer with the data provided.
            - Add the points
            - Replace everything else

        If it's a new email, create the customer and store their rewards.

        Endpoint:
            POST /customers

        See Also: _get_rewards_data() (line 18)
        """
        customer_data = json.loads(self.request.body)

        # Validate the request
        validated = _validate_customers_request(customer_data)

        # Return 422 if the errors exist in the valdated data
        if 'errors' in validated.keys():
            self.set_status(422)
            self.write(validated)
            return

        # Upsert a customer, create one if none are found, update one if one is found
        customer = DB.customers.update_one(
                {"email": validated["email"]},
                {"$set": validated},
                upsert=True
            )

        # Set the status to 200 if the customer is updated, 201 if the customer is created
        if customer.matched_count:
            self.set_status(200)
        else:
            self.set_status(201)

        # Send the json response
        self.write(json.dumps(validated, default=str))

